"""Testes do modo de voz avançado: frases, filtro de pensamento, fila, interrupção e ferramentas."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import simuladores  # noqa: E402

pytestmark = pytest.mark.timeout(60)


@pytest.fixture(scope="module")
def servidor():
    with simuladores.ServidorEmThread() as srv:
        yield srv


@pytest.fixture(scope="module")
def voz(servidor, tmp_path_factory):
    base = f"http://127.0.0.1:{servidor.porta}"
    os.environ.update({
        "LLM_URL": f"{base}/v1", "LLM_MODELO": "teste", "LLM_RESERVA_URL": f"{base}/v1", "LLM_RESERVA_MODELO": "reserva",
        "FALA_URL": f"{base}/v1", "FERRAMENTAS_URL": base, "ARQUIVOS_URL_PUBLICA": "http://192.168.0.10:3002",
        "CONVERSAS_DIR": str(tmp_path_factory.mktemp("conversas")), "NO_PROXY": "127.0.0.1,localhost",
    })
    spec = importlib.util.spec_from_file_location("voz_app", RAIZ / "servicos" / "voz" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["voz_app"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def cliente(voz):
    for v in simuladores.registro.values():
        v.clear()
    simuladores.app.state.atraso_imagem = 0.1
    voz.LLM_MODELO = "teste"
    with TestClient(voz.app) as c:
        yield c


# ---------------------------------------------------------------- unidades
def test_separador_de_frases(voz):
    sep = voz.SeparadorFrases()
    saida = []
    for pedaco in ["Olá! Tudo", " bem? O Dr. Silva che", "gou às 3.", "5 horas. Lista:\n1. pão\n", "Fim"]:
        saida += sep.adicionar(pedaco)
    saida += sep.finalizar()
    assert saida == ["Olá!", "Tudo bem?", "O Dr. Silva chegou às 3.5 horas.", "Lista:", "1. pão", "Fim"]


def test_primeira_frase_longa_sai_na_virgula(voz):
    sep = voz.SeparadorFrases()
    frases = sep.adicionar("Bom, essa é uma pergunta interessante e que tem várias respostas possíveis, dependendo do contexto em que")
    assert frases and frases[0].endswith(",")


def test_filtro_de_pensamento(voz):
    f = voz.FiltroPensamento()
    partes = ["Oi <thi", "nk>pensando muito</thi", "nk> tudo bem", "? <", "b>x</b>"]
    assert "".join(f.filtrar(p) for p in partes) == "Oi  tudo bem? <b>x</b>"


@pytest.mark.parametrize("frase, parar", [("Para.", True), ("pode parar!", True), ("Cancela", True), ("Esquece", True),
                                           ("para onde vamos?", False), ("parar de fumar faz bem?", False)])
def test_comando_parar(voz, frase, parar):
    assert voz.eh_comando_parar(frase) is parar


def test_ferramentas_lidas_do_openapi(cliente, voz):
    import httpx

    esquema = httpx.get(f"{voz.FERRAMENTAS_URL}/openapi.json").json()
    defs, rotas = voz.ferramentas_do_openapi(esquema)
    nomes = {d["function"]["name"] for d in defs}
    assert nomes == {"buscar_web", "ler_pagina", "gerar_imagem", "criar_documento", "data_hora"}
    img = next(d for d in defs if d["function"]["name"] == "gerar_imagem")["function"]["parameters"]
    assert img["required"] == ["descricao_em_ingles"]
    assert img["properties"]["formato"]["enum"] == ["quadrado", "retrato", "paisagem"]
    assert rotas["data_hora"] == ("GET", "/data_hora")


# ---------------------------------------------------------------- turnos completos (sem navegador)
def test_turno_simples_fala_frase_a_frase(cliente):
    r = cliente.post("/api/turno", json={"texto": "qual a capital?"}).json()
    assert "Brasília é a capital do Brasil." in r["resposta"]
    assert simuladores.registro["tts"] == ["Olá!", "Brasília é a capital do Brasil.", "Posso ajudar em algo mais?"]
    assert len(r["audios"]) == 3
    assert [e["tipo"] for e in r["eventos"] if e["tipo"].startswith("turno")] == ["turno_inicio", "turno_fim"]


def test_turno_com_imagem(cliente):
    r = cliente.post("/api/turno", json={"texto": "gera uma imagem de gato"}).json()
    tipos = [e["tipo"] for e in r["eventos"]]
    assert "imagem" in tipos
    img = next(e for e in r["eventos"] if e["tipo"] == "imagem")
    assert img["url"] == "/arquivos/imagem-gato.png"  # link relativo, funciona via HTTPS e pelo celular
    assert simuladores.registro["ferramentas"][0] == ("gerar_imagem", {"descricao_em_ingles": "a cat astronaut", "formato": "quadrado"})
    assert simuladores.registro["tts"][0].startswith("Certo, vou gerar a imagem")
    assert r["resposta"].endswith("Já está na tela.")
    papeis = [m["role"] for m in r["historico"]]
    assert papeis == ["user", "assistant", "tool", "assistant"]


def test_documento_e_download_pelo_proxy(cliente):
    r = cliente.post("/api/turno", json={"texto": "cria um documento com a lista", "com_voz": False}).json()
    arq = next(e for e in r["eventos"] if e["tipo"] == "arquivo")
    assert arq["url"] == "/arquivos/lista-abc.pdf"
    assert cliente.get(arq["url"]).content.startswith(b"%PDF")


def test_modelo_de_reserva_quando_o_principal_falha(cliente, voz):
    voz.LLM_MODELO = "quebrado"
    r = cliente.post("/api/turno", json={"texto": "oi", "com_voz": False}).json()
    assert "Brasília" in r["resposta"]
    assert [c["model"] for c in simuladores.registro["llm"]] == ["quebrado", "reserva"]
    assert any(e["tipo"] == "aviso" and "reserva" in e["texto"] for e in r["eventos"])


def test_pensamento_nao_e_falado(cliente):
    r = cliente.post("/api/turno", json={"texto": "pense bem", "com_voz": True}).json()
    assert r["resposta"] == "Resposta sem pensamento."
    assert simuladores.registro["tts"] == ["Resposta sem pensamento."]


def test_pedidos_acumulados_vao_juntos(cliente):
    r = cliente.post("/api/turno", json={"pedidos": ["gera uma imagem", "e que horas são?"], "com_voz": False}).json()
    pedidos = [e["pedido"] for e in r["eventos"] if e["tipo"] == "turno_inicio"]
    assert pedidos[0] == "gera uma imagem" or pedidos == ["gera uma imagem\ne que horas são?"]


# ---------------------------------------------------------------- WebSocket: interrupção e fila
def receber_ate(ws, condicao, limite=400):
    vistos = []
    for _ in range(limite):
        m = ws.receive()
        if m.get("type") == "websocket.close":
            break
        item = ("audio", m["bytes"]) if m.get("bytes") is not None else ("evento", json.loads(m["text"]))
        vistos.append(item)
        if condicao(item):
            return vistos
    raise AssertionError(f"condição não atingida; últimos: {[v[1] if v[0] == 'evento' else 'audio' for v in vistos[-15:]]}")


def evento(tipo, **campos):
    return lambda item: item[0] == "evento" and item[1]["tipo"] == tipo and all(item[1].get(k) == v for k, v in campos.items())


def test_interromper_resposta_falada(cliente):
    with cliente.websocket_connect("/ws") as ws:
        receber_ate(ws, evento("pronto"))
        ws.send_text(json.dumps({"tipo": "texto", "texto": "me dá uma resposta longa"}))
        receber_ate(ws, lambda i: i[0] == "audio")
        ws.send_text(json.dumps({"tipo": "inicio_fala"}))
        receber_ate(ws, evento("silenciado", turno=1))
        ws.send_bytes(simuladores.wav_de_teste(1))
        vistos = receber_ate(ws, evento("turno_fim", turno=2))
        eventos = [v[1] for v in vistos if v[0] == "evento"]
        assert {"tipo": "turno_fim", "turno": 1, "interrompido": True} in eventos
        assert any(e["tipo"] == "turno_inicio" and e["turno"] == 2 and e["pedido"] == "qual é a capital do Brasil?" for e in eventos)
    # O histórico enviado ao LLM no segundo turno registra a resposta interrompida
    ultima = simuladores.registro["llm"][-1]["messages"]
    assert any(m["role"] == "assistant" and "interrompido pelo usuário" in m["content"] for m in ultima)
    # Depois da interrupção, nenhuma frase longa nova foi sintetizada para o turno 1
    frases_longas = [t for t in simuladores.registro["tts"] if "resposta bem longa" in t]
    assert len(frases_longas) < 10


def test_fila_durante_tarefa_em_segundo_plano(cliente):
    simuladores.app.state.atraso_imagem = 2.0
    with cliente.websocket_connect("/ws") as ws:
        receber_ate(ws, evento("pronto"))
        ws.send_text(json.dumps({"tipo": "texto", "texto": "gera uma imagem de gato"}))
        receber_ate(ws, evento("ferramenta", status="inicio"))
        ws.send_text(json.dumps({"tipo": "inicio_fala"}))
        ws.send_bytes(simuladores.wav_de_teste(2))
        vistos = receber_ate(ws, evento("turno_fim", turno=2))
        eventos = [v[1] for v in vistos if v[0] == "evento"]
        # A imagem continuou, o pedido novo esperou na fila e o resultado foi anunciado em voz
        assert any(e["tipo"] == "aviso" and e["texto"].startswith("Anotado") for e in eventos)
        assert any(e["tipo"] == "estado" and e.get("fila") == ["e que horas são?"] for e in eventos)
        assert {"tipo": "turno_fim", "turno": 1, "interrompido": False} in [{k: e[k] for k in ("tipo", "turno", "interrompido")} for e in eventos if e["tipo"] == "turno_fim"]
        assert any(e["tipo"] == "imagem" for e in eventos)
        assert any(e["tipo"] == "turno_inicio" and e["pedido"] == "e que horas são?" for e in eventos)
    assert "Já está na tela." in simuladores.registro["tts"]


def test_comando_parar_por_voz(cliente):
    with cliente.websocket_connect("/ws") as ws:
        receber_ate(ws, evento("pronto"))
        ws.send_text(json.dumps({"tipo": "texto", "texto": "resposta longa por favor"}))
        receber_ate(ws, lambda i: i[0] == "audio")
        ws.send_text(json.dumps({"tipo": "inicio_fala"}))
        ws.send_bytes(simuladores.wav_de_teste(3))
        vistos = receber_ate(ws, evento("aviso", texto="Tudo bem, parei."))
        assert any(v[0] == "evento" and v[1]["tipo"] == "turno_fim" and v[1]["interrompido"] for v in vistos)


def test_ruido_retoma_a_fala(cliente):
    with cliente.websocket_connect("/ws") as ws:
        receber_ate(ws, evento("pronto"))
        ws.send_text(json.dumps({"tipo": "texto", "texto": "resposta longa"}))
        receber_ate(ws, lambda i: i[0] == "audio")
        ws.send_text(json.dumps({"tipo": "inicio_fala"}))
        receber_ate(ws, evento("silenciado", turno=1))
        ws.send_bytes(simuladores.wav_de_teste(4))  # transcrição vazia (tosse, ruído)
        receber_ate(ws, evento("retomado", turno=1))
        ws.send_text(json.dumps({"tipo": "cancelar"}))
        receber_ate(ws, evento("turno_fim", turno=1))


def test_interromper_audio_de_turno_ja_gerado(cliente):
    with cliente.websocket_connect("/ws") as ws:
        receber_ate(ws, evento("pronto"))
        ws.send_text(json.dumps({"tipo": "texto", "texto": "oi"}))
        receber_ate(ws, evento("turno_fim", turno=1))
        # O servidor já terminou, mas o navegador ainda tocava o áudio do turno 1
        ws.send_text(json.dumps({"tipo": "inicio_fala", "turno": 1}))
        receber_ate(ws, evento("turno_fim", turno=1, interrompido=True))
        ws.send_text(json.dumps({"tipo": "texto", "texto": "continua"}))
        receber_ate(ws, evento("turno_fim", turno=2))
    msgs = simuladores.registro["llm"][-1]["messages"]
    assert any(m["role"] == "assistant" and "antes do fim da fala" in m["content"] for m in msgs)
