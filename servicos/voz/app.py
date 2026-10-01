"""Modo de voz avançado: conversa contínua, full duplex, em português.

O navegador mantém o microfone aberto com detecção de voz (Silero VAD) e manda
cada fala completa por WebSocket. Aqui cada sessão tem:

* uma fila de pedidos: o que o usuário diz enquanto o assistente trabalha é
  enfileirado e respondido em seguida (pedidos acumulados vão juntos);
* um turno atual, que pode estar "pensando", "falando" ou "executando ferramenta";
* interrupção: ao detectar a voz do usuário o navegador corta o áudio na hora e
  avisa o servidor, que para de sintetizar aquele turno. Se o turno era só fala,
  ele é abandonado e o novo pedido é atendido; se havia uma ferramenta rodando
  (busca, imagem, documento), ela continua em segundo plano e o pedido novo entra na fila.

O LLM é chamado pela API compatível com OpenAI (Ollama ou llama.cpp) com as
ferramentas descritas no OpenAPI do serviço "ferramentas".
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import re
import struct
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("voz")
logging.basicConfig(level=os.getenv("NIVEL_LOG", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

LLM_URL = os.getenv("LLM_URL", "http://ollama:11434/v1").rstrip("/")
LLM_MODELO = os.getenv("LLM_MODELO", "qwen3:8b")
LLM_RESERVA_URL = os.getenv("LLM_RESERVA_URL", "http://ollama:11434/v1").rstrip("/")
LLM_RESERVA_MODELO = os.getenv("LLM_RESERVA_MODELO", "")
FALA_URL = os.getenv("FALA_URL", "http://fala:8000/v1").rstrip("/")
FERRAMENTAS_URL = os.getenv("FERRAMENTAS_URL", "http://ferramentas:8000").rstrip("/")
FERRAMENTAS_TOKEN = os.getenv("FERRAMENTAS_TOKEN", "")
ARQUIVOS_URL_PUBLICA = os.getenv("ARQUIVOS_URL_PUBLICA", "").rstrip("/")
TTS_VOZ = os.getenv("TTS_VOZ", "pf_dora")
WEBUI_URL = os.getenv("WEBUI_URL_PUBLICA", "")
FUSO = os.getenv("FUSO_HORARIO", "America/Sao_Paulo")
MAX_RODADAS = int(os.getenv("MAX_RODADAS_FERRAMENTAS", "6"))
MAX_MENSAGENS = int(os.getenv("MAX_MENSAGENS_HISTORICO", "30"))
PASTA_CONVERSAS = Path(os.getenv("CONVERSAS_DIR", "/dados/conversas"))
ESTATICOS = Path(__file__).parent / "static"

NOMES_FERRAMENTAS = {
    "buscar_web": "Pesquisando na internet",
    "ler_pagina": "Lendo a página",
    "gerar_imagem": "Gerando a imagem",
    "criar_documento": "Criando o documento",
    "data_hora": "Consultando a hora",
}
AVISOS_FERRAMENTAS = {
    "buscar_web": "Um instante, vou pesquisar.",
    "ler_pagina": "Vou abrir a página.",
    "gerar_imagem": "Certo, vou gerar a imagem. Pode continuar falando enquanto isso.",
    "criar_documento": "Certo, vou montar o documento.",
}
COMANDO_PARAR = re.compile(
    r"^(para|pare|parar|chega|cancela|cancelar|cancele|esquece|esqueca|deixa pra la|deixa para la|silencio|"
    r"fica quieto|fica quieta|pode parar|para tudo|cancela tudo)[.!,]*$"
)

PROMPT_SISTEMA = """Você é um assistente de voz em português do Brasil, simpático, natural e direto, \
conversando por áudio em tempo real com o usuário.

Regras de fala:
- Responda curto e natural, como numa conversa falada (uma a três frases), a não ser que peçam detalhes.
- Não use markdown, listas, tabelas, emojis nem links: tudo o que você escreve é lido em voz alta.
- Escreva números, datas e siglas do jeito que se fala.

Ferramentas:
- buscar_web: informações atuais (notícias, clima, preços, placares) ou que você não sabe com certeza. \
Depois de pesquisar, responda com as informações encontradas e cite a fonte pelo nome do site.
- ler_pagina: abrir um link quando os trechos da busca não bastarem.
- gerar_imagem: criar imagens; a descrição precisa ser em inglês e detalhada.
- criar_documento: gerar arquivo PDF, Word (docx), TXT ou Markdown com o conteúdo completo em markdown.
- data_hora: data e hora atuais.
- Ao terminar uma imagem ou documento, diga em uma frase que está pronto e aparece na tela; não leia links.

Conversa:
- Se o usuário interromper, abandone o assunto anterior e atenda ao novo pedido.
- Pedidos feitos enquanto você trabalhava chegam juntos numa mesma mensagem; atenda a todos.
- Hoje é {data}."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t).strip()


def eh_comando_parar(texto: str) -> bool:
    return bool(COMANDO_PARAR.match(_normalizar(texto)))


def _data_hoje() -> str:
    dias = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
    meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
    a = dt.datetime.now(ZoneInfo(FUSO))
    return f"{dias[a.weekday()]}, {a.day} de {meses[a.month - 1]} de {a.year}, {a:%H:%M}"


def _url_local(url: str) -> str:
    """Links do serviço de ferramentas passam a apontar para este servidor (funciona pelo celular e via HTTPS)."""
    if ARQUIVOS_URL_PUBLICA and url.startswith(ARQUIVOS_URL_PUBLICA):
        return url[len(ARQUIVOS_URL_PUBLICA):]
    m = re.search(r"(/arquivos/[^\s)\"']+)", url)
    return m.group(1) if m else url


class SeparadorFrases:
    """Corta o texto do LLM em frases à medida que chega, para a voz começar antes do fim da resposta."""

    FIM = re.compile(r"([.!?…]+[\"')\]]*)(\s+|$)|(\n+)")
    ABREV = re.compile(r"\b(sr|sra|dr|dra|prof|etc|ex|p|n|nº|av|vs|aprox|obs|tel)\.$", re.I)

    def __init__(self) -> None:
        self.buffer = ""
        self.primeira = True

    def adicionar(self, texto: str) -> list[str]:
        self.buffer += texto
        frases = []
        while True:
            m = None
            for cand in self.FIM.finditer(self.buffer):
                trecho = self.buffer[: cand.end()].strip()
                if self.ABREV.search(trecho.rstrip()) or re.search(r"\b\d+\.$", trecho):
                    continue  # "Dr." ou "1." no meio da frase
                if cand.group(2) == "" and cand.group(3) is None:
                    break  # pontuação no fim do buffer: pode ser "3." de "3.5", espera mais texto
                m = cand
                break
            if m is None:
                # A primeira frase longa sai já na vírgula, para reduzir a latência
                if self.primeira and len(self.buffer) > 90:
                    corte = self.buffer.rfind(", ", 40)
                    if corte > 0:
                        frases.append(self.buffer[: corte + 1].strip())
                        self.buffer = self.buffer[corte + 2 :]
                        self.primeira = False
                        continue
                break
            frase = self.buffer[: m.end()].strip()
            self.buffer = self.buffer[m.end() :]
            if frase:
                frases.append(frase)
                self.primeira = False
        return frases

    def finalizar(self) -> list[str]:
        resto = self.buffer.strip()
        self.buffer = ""
        return [resto] if resto else []


class FiltroPensamento:
    """Remove blocos <think>…</think> que alguns modelos emitem no meio do conteúdo (mesmo cortados entre pedaços)."""

    def __init__(self) -> None:
        self.dentro = False
        self.pendente = ""

    @staticmethod
    def _sufixo_parcial(t: str, marca: str) -> int:
        """Posição onde começa um pedaço incompleto da marca no fim de t (ou -1)."""
        for n in range(min(len(marca) - 1, len(t)), 0, -1):
            if marca.startswith(t[-n:]):
                return len(t) - n
        return -1

    def filtrar(self, texto: str) -> str:
        t = self.pendente + texto
        self.pendente = ""
        saida = []
        while t:
            marca = "</think>" if self.dentro else "<think>"
            pos = t.find(marca)
            if pos < 0:
                corte = self._sufixo_parcial(t, marca)
                if corte >= 0:
                    self.pendente = t[corte:]
                    t = t[:corte]
                if not self.dentro:
                    saida.append(t)
                break
            if not self.dentro:
                saida.append(t[:pos])
            t = t[pos + len(marca):]
            self.dentro = not self.dentro
        return "".join(saida)


# ---------------------------------------------------------------------------
# Ferramentas: definições lidas do OpenAPI do serviço de ferramentas
# ---------------------------------------------------------------------------
FERRAMENTAS_RESERVA = [
    {"type": "function", "function": {"name": "buscar_web", "description": "Pesquisa na internet.", "parameters": {"type": "object", "properties": {"consulta": {"type": "string"}, "quantidade": {"type": "integer", "default": 5}}, "required": ["consulta"]}}},
    {"type": "function", "function": {"name": "ler_pagina", "description": "Lê o texto de uma página.", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "gerar_imagem", "description": "Gera uma imagem a partir de uma descrição em inglês.", "parameters": {"type": "object", "properties": {"descricao_em_ingles": {"type": "string"}, "formato": {"type": "string", "enum": ["quadrado", "retrato", "paisagem"]}}, "required": ["descricao_em_ingles"]}}},
    {"type": "function", "function": {"name": "criar_documento", "description": "Cria um documento PDF, DOCX, TXT ou MD.", "parameters": {"type": "object", "properties": {"titulo": {"type": "string"}, "conteudo_markdown": {"type": "string"}, "formato": {"type": "string", "enum": ["pdf", "txt", "md", "docx"]}}, "required": ["titulo", "conteudo_markdown"]}}},
    {"type": "function", "function": {"name": "data_hora", "description": "Data e hora atuais.", "parameters": {"type": "object", "properties": {}}}},
]
ROTAS_FERRAMENTAS: dict[str, tuple[str, str]] = {
    "buscar_web": ("POST", "/buscar_web"),
    "ler_pagina": ("POST", "/ler_pagina"),
    "gerar_imagem": ("POST", "/gerar_imagem"),
    "criar_documento": ("POST", "/criar_documento"),
    "data_hora": ("GET", "/data_hora"),
}


def _limpar_esquema(esq: Any, componentes: dict) -> Any:
    if isinstance(esq, dict):
        if "$ref" in esq:
            nome = esq["$ref"].rsplit("/", 1)[-1]
            return _limpar_esquema(componentes.get(nome, {}), componentes)
        return {k: _limpar_esquema(v, componentes) for k, v in esq.items() if k != "title"}
    if isinstance(esq, list):
        return [_limpar_esquema(x, componentes) for x in esq]
    return esq


def ferramentas_do_openapi(esquema: dict) -> tuple[list[dict], dict[str, tuple[str, str]]]:
    componentes = esquema.get("components", {}).get("schemas", {})
    defs, rotas = [], {}
    for caminho, metodos in esquema.get("paths", {}).items():
        for metodo, op in metodos.items():
            nome = op.get("operationId")
            if not nome:
                continue
            corpo = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            params = _limpar_esquema(corpo, componentes) if corpo else {"type": "object", "properties": {}}
            descricao = " ".join(x for x in (op.get("summary"), op.get("description")) if x)
            defs.append({"type": "function", "function": {"name": nome, "description": descricao, "parameters": params}})
            rotas[nome] = (metodo.upper(), caminho)
    return defs, rotas


class Recursos:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(30, read=900))
        self.ferramentas = FERRAMENTAS_RESERVA
        self.rotas = dict(ROTAS_FERRAMENTAS)

    async def carregar_ferramentas(self) -> None:
        for _ in range(30):
            try:
                r = await self.http.get(f"{FERRAMENTAS_URL}/openapi.json", timeout=5)
                defs, rotas = ferramentas_do_openapi(r.json())
                if defs:
                    self.ferramentas, self.rotas = defs, rotas
                    log.info("Ferramentas carregadas: %s", ", ".join(rotas))
                    return
            except Exception as e:  # noqa: BLE001
                log.debug("Ferramentas ainda indisponíveis: %s", e)
            await asyncio.sleep(2)
        log.warning("Usando definições de ferramentas embutidas")

    async def transcrever(self, wav: bytes) -> str:
        r = await self.http.post(
            f"{FALA_URL}/audio/transcriptions",
            files={"file": ("fala.wav", wav, "audio/wav")},
            data={"model": "whisper-1", "language": "pt", "response_format": "json"},
            timeout=60,
        )
        r.raise_for_status()
        return (r.json().get("text") or "").strip()

    async def sintetizar(self, texto: str, voz: str, velocidade: float) -> bytes:
        r = await self.http.post(
            f"{FALA_URL}/audio/speech",
            json={"model": "kokoro", "input": texto, "voice": voz, "speed": velocidade, "response_format": "wav"},
            timeout=60,
        )
        r.raise_for_status()
        return r.content

    async def executar_ferramenta(self, nome: str, argumentos: dict) -> dict:
        if nome not in self.rotas:
            return {"erro": f"ferramenta desconhecida: {nome}"}
        metodo, caminho = self.rotas[nome]
        cab = {"Authorization": f"Bearer {FERRAMENTAS_TOKEN}"} if FERRAMENTAS_TOKEN else {}
        try:
            if metodo == "GET":
                r = await self.http.get(f"{FERRAMENTAS_URL}{caminho}", params=argumentos, headers=cab)
            else:
                r = await self.http.post(f"{FERRAMENTAS_URL}{caminho}", json=argumentos, headers=cab)
            if r.status_code >= 400:
                return {"erro": f"{r.status_code}: {r.text[:500]}"}
            return r.json()
        except Exception as e:  # noqa: BLE001
            return {"erro": str(e)}


recursos: Recursos | None = None


# ---------------------------------------------------------------------------
# Sessão de conversa
# ---------------------------------------------------------------------------
@dataclass
class Turno:
    id: int
    pedido: str
    fase: str = "pensando"  # pensando | falando | ferramenta
    silenciado: bool = False
    terminado: bool = False
    usou_ferramenta: bool = False
    falou: bool = False
    texto: str = ""
    texto_rodada: str = ""
    tarefa: asyncio.Task | None = None
    fila_tts: asyncio.Queue = field(default_factory=asyncio.Queue)
    inicio_historico: int = 0
    t0: float = field(default_factory=time.time)


class Sessao:
    def __init__(self, enviar_json, enviar_bytes, com_voz: bool = True) -> None:
        self.enviar_json = enviar_json
        self.enviar_bytes = enviar_bytes
        self.com_voz = com_voz
        self.historico: list[dict] = []
        self.fila: asyncio.Queue[str] = asyncio.Queue()
        self.pendentes: list[str] = []
        self.turno: Turno | None = None
        self.contador = 0
        self.voz = TTS_VOZ
        self.velocidade = 1.0
        self.anexos: dict[str, str] = {}
        self.id = uuid.uuid4().hex[:10]
        self.ocioso = asyncio.Event()
        self.ocioso.set()
        self.trabalhador = asyncio.create_task(self._trabalhar())

    # -------- envio --------
    async def evento(self, tipo: str, **dados) -> None:
        with contextlib.suppress(Exception):
            await self.enviar_json({"tipo": tipo, **dados})

    async def estado(self) -> None:
        t = self.turno
        if t is None or t.terminado:
            estado, detalhe = "ouvindo", ""
        else:
            estado, detalhe = t.fase, ""
        await self.evento("estado", estado=estado, detalhe=detalhe, fila=list(self.pendentes))

    async def _audio(self, turno_id: int, seq: int, wav: bytes) -> None:
        with contextlib.suppress(Exception):
            await self.enviar_bytes(struct.pack("<II", turno_id, seq) + wav)

    async def falar_aviso(self, texto: str) -> None:
        """Frase curta fora dos turnos (turno 0), tocada mesmo com outro turno silenciado."""
        await self.evento("aviso", texto=texto)
        if self.com_voz and recursos:
            with contextlib.suppress(Exception):
                await self._audio(0, 0, await recursos.sintetizar(texto, self.voz, self.velocidade))

    # -------- entrada do usuário --------
    async def receber_audio(self, wav: bytes) -> None:
        try:
            texto = await recursos.transcrever(wav)
        except Exception as e:  # noqa: BLE001
            log.warning("Transcrição falhou: %s", e)
            await self.evento("erro", mensagem="Não consegui transcrever o áudio.")
            await self.retomar_se_descartado()
            return
        if not texto:
            await self.retomar_se_descartado()
            return
        await self.evento("transcricao", texto=texto)
        await self.novo_pedido(texto)

    async def retomar_se_descartado(self) -> None:
        """Ruído ou tosse interrompeu a fala: volta a falar o resto da resposta."""
        t = self.turno
        if t and not t.terminado and t.silenciado and not self.pendentes:
            t.silenciado = False
            await self.evento("retomado", turno=t.id)

    async def inicio_fala(self, turno_tocando: int | None = None) -> None:
        t = self.turno
        if t and not t.terminado:
            t.silenciado = True
            await self.evento("silenciado", turno=t.id)
        elif t and turno_tocando == t.id:
            # O texto já tinha sido todo gerado, mas o navegador ainda tocava o áudio:
            # registra que o usuário não ouviu o fim da resposta.
            ultima = self.historico[-1] if self.historico else None
            if ultima and ultima["role"] == "assistant" and not ultima.get("tool_calls") and "(interrompido" not in ultima["content"]:
                ultima["content"] += " … (interrompido pelo usuário antes do fim da fala)"
            await self.evento("turno_fim", turno=t.id, interrompido=True)

    async def novo_pedido(self, texto: str) -> None:
        if eh_comando_parar(texto):
            await self.cancelar_tudo()
            await self.falar_aviso("Tudo bem, parei.")
            return
        t = self.turno
        if t and not t.terminado:
            if t.fase == "ferramenta":
                # Há uma tarefa em segundo plano (busca, imagem, documento): ela continua,
                # o pedido novo entra na fila e o resultado ainda será anunciado em voz.
                self._enfileirar(texto)
                if t.silenciado:
                    t.silenciado = False
                    await self.evento("retomado", turno=t.id)
                await self.estado()
                if len(self.pendentes) == 1:
                    await self.falar_aviso("Anotado, já vejo isso.")
            elif t.silenciado:
                # Interrompeu enquanto o assistente pensava ou falava: abandona e atende o pedido novo.
                # Se ele ainda não tinha dito nada nem usado ferramenta, junta os dois pedidos.
                anterior = t.pedido if not (t.texto.strip() or t.usou_ferramenta) else None
                await self.cancelar_turno(t)
                self._enfileirar(f"{anterior}\n{texto}" if anterior else texto)
            else:
                # Texto digitado no meio de uma resposta: espera a vez
                self._enfileirar(texto)
                await self.estado()
            return
        self._enfileirar(texto)

    def _enfileirar(self, texto: str) -> None:
        self.pendentes.append(texto)
        self.ocioso.clear()
        self.fila.put_nowait(texto)

    async def cancelar_turno(self, t: Turno) -> None:
        t.silenciado = True
        if t.tarefa and not t.tarefa.done():
            t.tarefa.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t.tarefa
        # O histórico fica só com o que de fato aconteceu: o pedido, as ferramentas que
        # terminaram (o modelo precisa saber que a imagem ou o documento já existem) e o
        # trecho que chegou a ser dito.
        base, trecho = self.historico[: t.inicio_historico], self.historico[t.inicio_historico :]
        corte, abertas = 0, set()
        for i, m in enumerate(trecho):
            if m["role"] == "assistant" and m.get("tool_calls"):
                abertas |= {c["id"] for c in m["tool_calls"]}
            elif m["role"] == "tool":
                abertas.discard(m.get("tool_call_id"))
                if not abertas:
                    corte = i + 1
        mantidos = trecho[:corte]
        dito = t.texto_rodada.strip()
        if dito and not mantidos:
            mantidos = [{"role": "user", "content": t.pedido}]
        if dito:
            mantidos.append({"role": "assistant", "content": dito + " … (interrompido pelo usuário)"})
        self.historico = base + mantidos
        await self.evento("turno_fim", turno=t.id, interrompido=True)

    async def cancelar_tudo(self) -> None:
        while not self.fila.empty():
            self.fila.get_nowait()
        self.pendentes.clear()
        if self.turno and not self.turno.terminado:
            await self.cancelar_turno(self.turno)
        await self.estado()

    def anexar(self, nome: str, texto: str) -> None:
        self.anexos[nome] = texto[:12000]

    # -------- processamento --------
    async def _trabalhar(self) -> None:
        while True:
            primeiro = await self.fila.get()
            juntos = [primeiro]
            while not self.fila.empty():
                juntos.append(self.fila.get_nowait())
            for x in juntos:
                with contextlib.suppress(ValueError):
                    self.pendentes.remove(x)
            pedido = "\n".join(juntos)
            self.contador += 1
            t = Turno(id=self.contador, pedido=pedido, inicio_historico=len(self.historico))
            self.turno = t
            await self.evento("turno_inicio", turno=t.id, pedido=pedido)
            await self.estado()
            t.tarefa = asyncio.create_task(self._executar(t))
            await asyncio.wait({t.tarefa})
            t.terminado = True
            if not t.tarefa.cancelled():
                await self.evento("turno_fim", turno=t.id, interrompido=False, segundos=round(time.time() - t.t0, 2))
            self._salvar()
            await self.estado()
            if self.fila.empty():
                self.ocioso.set()

    def _mensagens(self) -> list[dict]:
        sistema = PROMPT_SISTEMA.format(data=_data_hoje())
        if "qwen3" in LLM_MODELO.lower() and "omni" not in LLM_MODELO.lower():
            sistema += "\n/no_think"
        if self.anexos:
            sistema += "\n\nArquivos que o usuário enviou (use quando ele perguntar sobre eles):"
            for nome, texto in self.anexos.items():
                sistema += f"\n\n### {nome}\n{texto}"
        hist = self.historico[-MAX_MENSAGENS:]
        while hist and hist[0]["role"] != "user":
            hist = hist[1:]
        return [{"role": "system", "content": sistema}, *hist]

    async def _executar(self, t: Turno) -> None:
        self.historico.append({"role": "user", "content": t.pedido})
        locutor = asyncio.create_task(self._locutor(t))
        try:
            for _ in range(MAX_RODADAS):
                t.fase = "pensando"
                texto, chamadas = await self._llm(t)
                if not chamadas:
                    self.historico.append({"role": "assistant", "content": texto})
                    break
                self.historico.append({"role": "assistant", "content": texto or "", "tool_calls": chamadas})
                t.fase = "ferramenta"
                t.usou_ferramenta = True
                for ch in chamadas:
                    nome = ch["function"]["name"]
                    try:
                        args = json.loads(ch["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if not t.falou and not t.texto.strip() and nome in AVISOS_FERRAMENTAS:
                        await t.fila_tts.put(AVISOS_FERRAMENTAS[nome])
                    await self.evento("ferramenta", turno=t.id, nome=nome, status="inicio", descricao=NOMES_FERRAMENTAS.get(nome, nome), argumentos=args)
                    await self.evento("estado", estado="ferramenta", detalhe=NOMES_FERRAMENTAS.get(nome, nome), fila=list(self.pendentes))
                    resultado = await recursos.executar_ferramenta(nome, args)
                    await self._mostrar_resultado(t, nome, args, resultado)
                    self.historico.append({"role": "tool", "tool_call_id": ch["id"], "name": nome, "content": json.dumps(resultado, ensure_ascii=False)[:8000]})
            else:
                await t.fila_tts.put("Precisei de passos demais para isso. Pode reformular?")
        except asyncio.CancelledError:
            locutor.cancel()
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("Erro no turno")
            await self.evento("erro", mensagem=f"Erro: {e}")
            await t.fila_tts.put("Desculpe, tive um problema para responder agora.")
        await t.fila_tts.put(None)
        with contextlib.suppress(asyncio.CancelledError):
            await locutor

    async def _mostrar_resultado(self, t: Turno, nome: str, args: dict, r: dict) -> None:
        if "erro" in r:
            await self.evento("ferramenta", turno=t.id, nome=nome, status="erro", resumo=str(r["erro"])[:300])
            return
        resumo = ""
        if nome == "gerar_imagem" and r.get("url"):
            await self.evento("imagem", turno=t.id, url=_url_local(r["url"]), descricao=args.get("descricao_em_ingles", ""))
        elif nome == "criar_documento" and r.get("url"):
            await self.evento("arquivo", turno=t.id, url=_url_local(r["url"]), nome=r.get("nome_arquivo", "documento"))
        elif nome == "buscar_web":
            fontes = [{"titulo": x.get("titulo"), "url": x.get("url")} for x in r.get("resultados", [])[:5]]
            await self.evento("fontes", turno=t.id, fontes=fontes)
            resumo = f"{len(fontes)} resultados"
        await self.evento("ferramenta", turno=t.id, nome=nome, status="fim", resumo=resumo)

    async def _llm(self, t: Turno) -> tuple[str, list[dict]]:
        tentativas = [(LLM_URL, LLM_MODELO)]
        if LLM_RESERVA_MODELO and LLM_RESERVA_MODELO != LLM_MODELO:
            tentativas.append((LLM_RESERVA_URL, LLM_RESERVA_MODELO))
        ultimo_erro: Exception | None = None
        for i, (url, modelo) in enumerate(tentativas):
            try:
                return await self._llm_stream(t, url, modelo)
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                ultimo_erro = e
                if i + 1 < len(tentativas):
                    log.warning("LLM %s falhou (%s); usando o modelo de reserva %s", modelo, e, tentativas[i + 1][1])
                    await self.evento("aviso", texto=f"O modelo principal falhou ({type(e).__name__}); usando o modelo de reserva.")
        raise RuntimeError(f"LLM indisponível: {ultimo_erro}")

    async def _llm_stream(self, t: Turno, url: str, modelo: str) -> tuple[str, list[dict]]:
        corpo = {
            "model": modelo,
            "messages": self._mensagens(),
            "tools": recursos.ferramentas,
            "stream": True,
            "temperature": 0.6,
        }
        separador = SeparadorFrases()
        filtro = FiltroPensamento()
        texto = ""
        t.texto_rodada = ""
        chamadas: dict[int, dict] = {}
        async with recursos.http.stream("POST", f"{url}/chat/completions", json=corpo, timeout=httpx.Timeout(30, read=300)) as r:
            if r.status_code >= 400:
                await r.aread()
                r.raise_for_status()
            async for linha in r.aiter_lines():
                if not linha.startswith("data:"):
                    continue
                dado = linha[5:].strip()
                if dado == "[DONE]":
                    break
                try:
                    pedaco = json.loads(dado)
                except json.JSONDecodeError:
                    continue
                for escolha in pedaco.get("choices", []):
                    delta = escolha.get("delta") or {}
                    conteudo = filtro.filtrar(delta.get("content") or "")
                    if conteudo:
                        if not texto:
                            conteudo = conteudo.lstrip()
                        texto += conteudo
                        t.texto += conteudo
                        t.texto_rodada += conteudo
                        t.fase = "falando" if t.fase == "pensando" else t.fase
                        await self.evento("delta", turno=t.id, texto=conteudo)
                        for frase in separador.adicionar(conteudo):
                            await t.fila_tts.put(frase)
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", len(chamadas))
                        atual = chamadas.setdefault(idx, {"id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}", "type": "function", "function": {"name": "", "arguments": ""}})
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            atual["function"]["name"] += fn["name"]
                        if fn.get("arguments"):
                            a = fn["arguments"]
                            atual["function"]["arguments"] += a if isinstance(a, str) else json.dumps(a)
        for frase in separador.finalizar():
            await t.fila_tts.put(frase)
        if texto:
            t.texto += "\n"
            await self.evento("delta", turno=t.id, texto="\n")
        return texto.strip(), [chamadas[k] for k in sorted(chamadas)]

    async def _locutor(self, t: Turno) -> None:
        """Sintetiza as frases do turno em ordem, enquanto o LLM continua gerando."""
        seq = 0
        while True:
            frase = await t.fila_tts.get()
            if frase is None:
                return
            if t.silenciado or not self.com_voz:
                continue
            try:
                wav = await recursos.sintetizar(frase, self.voz, self.velocidade)
            except Exception as e:  # noqa: BLE001
                log.warning("Síntese falhou: %s", e)
                continue
            if t.silenciado:
                continue
            seq += 1
            t.falou = True
            await self._audio(t.id, seq, wav)

    def _salvar(self) -> None:
        try:
            PASTA_CONVERSAS.mkdir(parents=True, exist_ok=True)
            arq = PASTA_CONVERSAS / f"{dt.date.today():%Y-%m-%d}-{self.id}.json"
            arq.write_text(json.dumps(self.historico, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    async def fechar(self) -> None:
        self.trabalhador.cancel()
        if self.turno and self.turno.tarefa:
            self.turno.tarefa.cancel()


# ---------------------------------------------------------------------------
# Aplicação web
# ---------------------------------------------------------------------------
@contextlib.asynccontextmanager
async def _ciclo(_app: FastAPI):
    global recursos
    recursos = Recursos()
    tarefa = asyncio.create_task(recursos.carregar_ferramentas())
    yield
    tarefa.cancel()
    await recursos.http.aclose()


app = FastAPI(title="Modo de voz avançado", lifespan=_ciclo)
app.mount("/static", StaticFiles(directory=str(ESTATICOS)), name="static")


@app.get("/")
async def pagina() -> FileResponse:
    return FileResponse(ESTATICOS / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/config.json")
async def configuracao() -> dict:
    return {"webui": WEBUI_URL, "voz": TTS_VOZ, "modelo": LLM_MODELO}


@app.get("/health")
async def saude() -> JSONResponse:
    estado = {"voz": "ok", "modelo": LLM_MODELO, "ferramentas": list(recursos.rotas) if recursos else []}
    return JSONResponse(estado)


@app.get("/arquivos/{nome}")
async def arquivo(nome: str) -> Response:
    if "/" in nome or ".." in nome:
        raise HTTPException(400, "nome inválido")
    r = await recursos.http.get(f"{FERRAMENTAS_URL}/arquivos/{nome}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, "arquivo não encontrado")
    return Response(r.content, media_type=r.headers.get("content-type", "application/octet-stream"),
                    headers={"Content-Disposition": f'inline; filename="{nome}"'})


@app.post("/api/extrair")
async def extrair(arquivo: UploadFile = File(...)) -> dict:
    dados = await arquivo.read()
    cab = {"Authorization": f"Bearer {FERRAMENTAS_TOKEN}"} if FERRAMENTAS_TOKEN else {}
    r = await recursos.http.post(f"{FERRAMENTAS_URL}/extrair_texto", files={"arquivo": (arquivo.filename, dados)}, headers=cab)
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text[:300])
    return r.json()


async def _turno_sem_navegador(pedidos: list[str], com_voz: bool, timeout: float, wav: bytes | None = None) -> dict:
    """Executa pedidos sem navegador e devolve todos os eventos com tempos (teste de ponta a ponta)."""
    eventos: list[dict] = []
    audios: list[dict] = []
    t0 = time.time()

    async def enviar_json(e: dict) -> None:
        e["t"] = round(time.time() - t0, 3)
        eventos.append(e)

    async def enviar_bytes(b: bytes) -> None:
        turno, seq = struct.unpack("<II", b[:8])
        audios.append({"turno": turno, "seq": seq, "bytes": len(b) - 8, "t": round(time.time() - t0, 3)})

    s = Sessao(enviar_json, enviar_bytes, com_voz=com_voz)
    try:
        if wav is not None:
            await s.receber_audio(wav)
        for pedido in pedidos:
            await s.novo_pedido(pedido)
        await asyncio.wait_for(s.ocioso.wait(), timeout=timeout)
    finally:
        await s.fechar()
    resposta = "".join(e["texto"] for e in eventos if e["tipo"] == "delta").strip()
    transcricao = next((e["texto"] for e in eventos if e["tipo"] == "transcricao"), None)
    return {"transcricao": transcricao, "resposta": resposta, "eventos": eventos, "audios": audios,
            "historico": s.historico, "segundos": round(time.time() - t0, 2),
            "primeiro_audio_s": audios[0]["t"] if audios else None}


@app.post("/api/turno")
async def turno_de_teste(request: Request) -> dict:
    corpo = await request.json()
    pedidos = corpo.get("pedidos") or [corpo.get("texto", "")]
    return await _turno_sem_navegador(pedidos, bool(corpo.get("com_voz", True)), float(corpo.get("timeout", 900)))


@app.post("/api/turno_audio")
async def turno_de_teste_por_audio(arquivo: UploadFile = File(...)) -> dict:
    """Como /api/turno, mas a pergunta chega em áudio: Whisper → LLM → Kokoro, medindo a latência."""
    return await _turno_sem_navegador([], True, 900, wav=await arquivo.read())


@app.websocket("/ws")
async def conversa(ws: WebSocket) -> None:
    await ws.accept()
    trava = asyncio.Lock()

    async def enviar_json(d: dict) -> None:
        async with trava:
            await ws.send_text(json.dumps(d, ensure_ascii=False))

    async def enviar_bytes(b: bytes) -> None:
        async with trava:
            await ws.send_bytes(b)

    s = Sessao(enviar_json, enviar_bytes)
    tarefas: set[asyncio.Task] = set()
    await s.evento("pronto", sessao=s.id, voz=s.voz, modelo=LLM_MODELO)
    await s.estado()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes"):
                tarefa = asyncio.create_task(s.receber_audio(msg["bytes"]))
                tarefas.add(tarefa)
                tarefa.add_done_callback(tarefas.discard)
                continue
            try:
                d = json.loads(msg.get("text") or "{}")
            except json.JSONDecodeError:
                continue
            tipo = d.get("tipo")
            if tipo == "inicio_fala":
                await s.inicio_fala(d.get("turno"))
            elif tipo == "fala_descartada":
                await s.retomar_se_descartado()
            elif tipo == "texto" and d.get("texto", "").strip():
                await s.evento("transcricao", texto=d["texto"].strip())
                await s.novo_pedido(d["texto"].strip())
            elif tipo == "cancelar":
                await s.cancelar_tudo()
            elif tipo == "config":
                s.voz = d.get("voz") or s.voz
                s.velocidade = float(d.get("velocidade") or s.velocidade)
            elif tipo == "anexo":
                s.anexar(d.get("nome", "arquivo"), d.get("texto", ""))
                await s.evento("anexo_ok", nome=d.get("nome"))
            elif tipo == "nova_conversa":
                await s.cancelar_tudo()
                s.historico.clear()
                s.anexos.clear()
                await s.evento("conversa_limpa")
    except WebSocketDisconnect:
        pass
    finally:
        for tarefa in tarefas:
            tarefa.cancel()
        await s.fechar()
