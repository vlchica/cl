#!/usr/bin/env python3
"""Teste real de ponta a ponta do assistente instalado.

1. Ouvir (síntese):     Open WebUI → serviço de fala (Kokoro) gera a pergunta em áudio
2. Falar (transcrição): Open WebUI → Whisper transcreve esse áudio de volta
3. Conversa:            Open WebUI → LLM responde em português
4. Voz completa:        modo de voz recebe o áudio, transcreve, responde e fala (mede a latência)
5. Busca na web:        SearXNG direto e pelo assistente (ferramenta buscar_web)
6. Imagem:              Open WebUI → ComfyUI, e pelo assistente (ferramenta gerar_imagem)
7. Documento:           PDF pelo assistente e TXT pela ferramenta direta
8. VRAM por componente

Gera relatorio-teste.md e estado/teste.json. Uso: python3 scripts/teste_ponta_a_ponta.py
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sys
import time
import traceback
import unicodedata

from comum import RAIZ, http, ler_env, log, multipart
from medir_vram import medir, tabela_md


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower()) if unicodedata.category(c) != "Mn")


class Teste:
    def __init__(self, env: dict) -> None:
        self.env = env
        self.webui = f"http://localhost:{env.get('PORTA_WEBUI', '3000')}"
        self.voz = f"http://localhost:{env.get('PORTA_VOZ', '3001')}"
        self.ferr = f"http://localhost:{env.get('PORTA_ARQUIVOS', '3002')}"
        self.cab = {"Authorization": f"Bearer {env.get('OPENWEBUI_API_KEY', '')}"}
        self.cab_ferr = {"Authorization": f"Bearer {env.get('FERRAMENTAS_TOKEN', '')}"}
        self.resultados: list[dict] = []
        self.audio_pergunta: bytes = b""

    def etapa(self, nome: str, func) -> None:
        log(nome, "passo")
        t0 = time.time()
        try:
            detalhe = func() or ""
            ok = True
        except AssertionError as e:
            ok, detalhe = False, str(e)
        except Exception as e:  # noqa: BLE001
            ok, detalhe = False, f"{type(e).__name__}: {e}"
            traceback.print_exc()
        segundos = round(time.time() - t0, 1)
        self.resultados.append({"etapa": nome, "ok": ok, "detalhe": detalhe, "segundos": segundos})
        log(f"{nome}: {detalhe} ({segundos} s)", "ok" if ok else "erro")

    # ------------------------------------------------------------------ etapas
    def ouvir(self) -> str:
        frase = f"Olá! Qual é a capital do Brasil? Teste número {random.randint(100, 999)}."
        tempos = []
        for _ in range(2):  # a primeira chamada aquece; a segunda mede a latência real
            t0 = time.time()
            status, audio = http("POST", f"{self.webui}/api/v1/audio/speech",
                                 {"input": frase, "voice": self.env.get("TTS_VOZ", "pf_dora"), "model": "kokoro"},
                                 cabecalhos=self.cab, timeout=120, bruto=True)
            tempos.append(time.time() - t0)
            assert status == 200 and len(audio) > 2000, f"síntese falhou ({status})"
            frase = frase.replace("Teste", "Verificação")
        self.audio_pergunta = audio
        self.latencia_tts = round(tempos[-1], 2)
        assert tempos[-1] < 3, f"síntese lenta demais: {tempos[-1]:.2f} s"
        meta = "dentro da meta de 1 s" if tempos[-1] < 1 else "ACIMA da meta de 1 s"
        return f"{len(audio) // 1024} KB de MP3 em {tempos[-1]:.2f} s ({meta})"

    def falar(self) -> str:
        assert self.audio_pergunta, "sem áudio da etapa anterior"
        corpo, tipo = multipart({"language": "pt"}, {"file": ("pergunta.mp3", self.audio_pergunta, "audio/mpeg")})
        t0 = time.time()
        status, r = http("POST", f"{self.webui}/api/v1/audio/transcriptions", corpo, cabecalhos={**self.cab, "Content-Type": tipo}, timeout=120)
        s = time.time() - t0
        assert status == 200 and isinstance(r, dict), f"transcrição falhou ({status}): {str(r)[:200]}"
        texto = r.get("text", "")
        assert "capital" in _norm(texto) and "brasil" in _norm(texto), f"transcrição inesperada: {texto!r}"
        return f"Whisper ouviu “{texto}” em {s:.2f} s"

    def conversar(self) -> str:
        modelo = self.env.get("LLM_MODELO_ATIVO")
        t0 = time.time()
        status, r = http("POST", f"{self.webui}/api/chat/completions", {
            "model": modelo, "stream": False,
            "messages": [{"role": "user", "content": "Responda em uma frase curta: qual é a capital do Brasil?"}],
        }, cabecalhos=self.cab, timeout=600)
        assert status == 200 and isinstance(r, dict), f"chat falhou ({status}): {str(r)[:300]}"
        texto = r["choices"][0]["message"]["content"]
        assert "brasilia" in _norm(texto), f"resposta inesperada: {texto[:200]!r}"
        return f"{modelo}: “{texto.strip()[:120]}” em {time.time() - t0:.1f} s"

    def voz_completa(self) -> str:
        assert self.audio_pergunta, "sem áudio da etapa de síntese"
        corpo, tipo = multipart({}, {"arquivo": ("pergunta.mp3", self.audio_pergunta, "audio/mpeg")})
        status, r = http("POST", f"{self.voz}/api/turno_audio", corpo, cabecalhos={"Content-Type": tipo}, timeout=600)
        assert status == 200 and isinstance(r, dict), f"modo de voz falhou ({status}): {str(r)[:300]}"
        assert r.get("transcricao"), "o modo de voz não transcreveu o áudio"
        assert "brasilia" in _norm(r["resposta"]), f"resposta inesperada: {r['resposta'][:200]!r}"
        assert r.get("primeiro_audio_s") is not None, "nenhum áudio de resposta foi gerado"
        self.latencia_voz = r["primeiro_audio_s"]
        return (f"ouviu “{r['transcricao']}”, respondeu “{r['resposta'][:80]}” e começou a falar "
                f"{r['primeiro_audio_s']:.2f} s depois do fim da pergunta ({len(r['audios'])} trechos de áudio)")

    def busca(self) -> str:
        status, r = http("POST", f"{self.ferr}/buscar_web", {"consulta": "notícias de hoje no Brasil", "quantidade": 5}, cabecalhos=self.cab_ferr, timeout=60)
        assert status == 200 and r.get("resultados"), f"SearXNG sem resultados ({status}): {str(r)[:200]}"
        direto = len(r["resultados"])
        status, t = http("POST", f"{self.voz}/api/turno", {
            "texto": "Pesquise na internet a cotação do dólar hoje e me diga o valor em uma frase.", "com_voz": False,
        }, timeout=600)
        assert status == 200, f"turno falhou ({status})"
        usadas = [e for e in t["eventos"] if e["tipo"] == "ferramenta" and e["nome"] == "buscar_web"]
        assert any(e["status"] == "fim" for e in usadas), "o assistente não usou (ou não concluiu) a busca na web"
        return f"SearXNG: {direto} resultados; assistente pesquisou e respondeu “{t['resposta'][:100]}”"

    def imagem(self) -> str:
        if self.env.get("IMAGENS_ATIVAS") != "true":
            return "geração de imagens desativada nesta máquina (sem memória suficiente)"
        t0 = time.time()
        status, r = http("POST", f"{self.webui}/api/v1/images/generations",
                         {"prompt": "a cute orange cat astronaut floating in space, digital art"}, cabecalhos=self.cab, timeout=1800)
        assert status == 200 and r, f"Open WebUI não gerou a imagem ({status}): {str(r)[:300]}"
        s1 = time.time() - t0
        t0 = time.time()
        status, t = http("POST", f"{self.voz}/api/turno", {"texto": "Gere uma imagem de um farol à beira-mar ao pôr do sol.", "com_voz": False}, timeout=1800)
        img = next((e for e in t.get("eventos", []) if e["tipo"] == "imagem"), None) if isinstance(t, dict) else None
        assert img, f"o assistente não gerou imagem: {str(t)[:300]}"
        status, png = http("GET", f"{self.voz}{img['url']}", bruto=True, timeout=60)
        assert status == 200 and png[:8] == b"\x89PNG\r\n\x1a\n", "imagem gerada não pôde ser baixada"
        return f"Open WebUI: {s1:.1f} s; assistente por voz: {time.time() - t0:.1f} s ({len(png) // 1024} KB)"

    def documento(self) -> str:
        status, t = http("POST", f"{self.voz}/api/turno", {
            "texto": "Crie um documento PDF chamado Lista de compras com arroz, feijão, café e pão de queijo.", "com_voz": False,
        }, timeout=600)
        arq = next((e for e in t.get("eventos", []) if e["tipo"] == "arquivo"), None) if isinstance(t, dict) else None
        assert arq, f"o assistente não criou o documento: {str(t)[:300]}"
        status, pdf = http("GET", f"{self.voz}{arq['url']}", bruto=True, timeout=60)
        assert status == 200 and pdf.startswith(b"%PDF"), "o PDF não pôde ser baixado"
        status, r = http("POST", f"{self.ferr}/criar_documento", {"titulo": "Teste", "conteudo_markdown": "# Teste\n\nAcentuação: ação, maçã.", "formato": "txt"}, cabecalhos=self.cab_ferr)
        assert status == 200, f"TXT falhou ({status})"
        status, txt = http("GET", r["url"].replace(f"http://{self.env.get('HOST_IP')}", "http://localhost"), bruto=True)
        assert "maçã" in txt.decode("utf-8"), "TXT sem acentuação correta"
        return f"PDF “{arq['nome']}” ({len(pdf) // 1024} KB) e TXT criados e baixados"

    def vram(self) -> str:
        self.medicao = medir(self.env)
        if not self.medicao["gpus"]:
            return "sem GPU NVIDIA (tudo na CPU)"
        partes = [f"{k}: {v / 1024:.1f} GB" for k, v in sorted(self.medicao["componentes"].items(), key=lambda x: -x[1])]
        return "; ".join(partes) or "não consegui medir por componente"

    # ------------------------------------------------------------------ relatório
    def relatorio(self) -> str:
        ok = sum(r["ok"] for r in self.resultados)
        l = [f"# Teste de ponta a ponta — {dt.datetime.now():%d/%m/%Y %H:%M}", "",
             f"**{ok} de {len(self.resultados)} etapas OK.**", "", "| Etapa | Resultado | Tempo |", "|---|---|---:|"]
        for r in self.resultados:
            l.append(f"| {r['etapa']} | {'✅' if r['ok'] else '❌'} {r['detalhe']} | {r['segundos']} s |")
        l += ["", "## VRAM por componente", "", tabela_md(getattr(self, "medicao", {"gpus": [], "componentes": {}, "metodo": ""}))]
        return "\n".join(l) + "\n"


def executar(env: dict | None = None) -> Teste:
    env = env or ler_env()
    t = Teste(env)
    t.etapa("1. Ouvir (síntese de voz)", t.ouvir)
    t.etapa("2. Falar (reconhecimento de voz)", t.falar)
    t.etapa("3. Conversa com o LLM", t.conversar)
    t.etapa("4. Voz completa (áudio → resposta falada)", t.voz_completa)
    t.etapa("5. Busca na web", t.busca)
    t.etapa("6. Geração de imagem", t.imagem)
    t.etapa("7. Geração de documento", t.documento)
    t.etapa("8. VRAM por componente", t.vram)
    (RAIZ / "relatorio-teste.md").write_text(t.relatorio(), encoding="utf-8")
    (RAIZ / "estado").mkdir(exist_ok=True)
    (RAIZ / "estado" / "teste.json").write_text(json.dumps({"resultados": t.resultados, "vram": getattr(t, "medicao", None)}, ensure_ascii=False, indent=2), encoding="utf-8")
    return t


if __name__ == "__main__":
    teste = executar()
    print("\n" + teste.relatorio())
    sys.exit(0 if all(r["ok"] for r in teste.resultados) else 1)
