"""Servidores falsos (LLM compatível com OpenAI, fala e ferramentas) para testar o modo de voz sem GPU."""
from __future__ import annotations

import asyncio
import io
import json
import socket
import threading
import time

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

app = FastAPI(openapi_url=None)  # a rota /openapi.json abaixo imita o serviço de ferramentas
registro: dict[str, list] = {"llm": [], "ferramentas": [], "tts": [], "stt": []}

# O "áudio" do usuário nos testes é um WAV cujo comprimento codifica a frase
FRASES_STT = {1: "qual é a capital do Brasil?", 2: "e que horas são?", 3: "para", 4: ""}


def wav_de_teste(codigo: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(1600 * codigo, dtype=np.float32), 16000, format="WAV")
    return buf.getvalue()


def _sse(pedacos: list[dict], atraso: float = 0.0):
    async def gerar():
        for p in pedacos:
            if atraso:
                await asyncio.sleep(atraso)
            yield f"data: {json.dumps(p, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gerar(), media_type="text/event-stream")


def _texto(t: str) -> dict:
    return {"choices": [{"index": 0, "delta": {"content": t}}]}


def _chamada(nome: str, args: dict) -> dict:
    return {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": f"call_{nome}", "type": "function", "function": {"name": nome, "arguments": json.dumps(args)}}]}}]}


@app.post("/v1/chat/completions")
async def llm(request: Request):
    corpo = await request.json()
    registro["llm"].append(corpo)
    msgs = corpo["messages"]
    ultima_usuario = next(m["content"] for m in reversed(msgs) if m["role"] == "user")
    ja_usou_ferramenta = msgs[-1]["role"] == "tool"
    if corpo["model"] == "quebrado":
        return JSONResponse({"error": "out of memory"}, status_code=500)
    if "imagem" in ultima_usuario and not ja_usou_ferramenta:
        return _sse([_chamada("gerar_imagem", {"descricao_em_ingles": "a cat astronaut", "formato": "quadrado"})])
    if "documento" in ultima_usuario and not ja_usou_ferramenta:
        return _sse([_chamada("criar_documento", {"titulo": "Lista", "conteudo_markdown": "- pão", "formato": "pdf"})])
    if ja_usou_ferramenta:
        return _sse([_texto("Pronto! "), _texto("Já está na tela.")])
    if "longa" in ultima_usuario:
        frases = [f"Esta é a frase número {i} de uma resposta bem longa. " for i in range(1, 30)]
        return _sse([_texto(f) for f in frases], atraso=0.15)
    if "pense" in ultima_usuario:
        return _sse([_texto("<thi"), _texto("nk>raciocínio interno</think>"), _texto("Resposta sem pensamento.")])
    return _sse([_texto("Olá! "), _texto("Brasília é a capital do Brasil. "), _texto("Posso ajudar em algo mais?")])


@app.post("/v1/audio/transcriptions")
async def stt(file: UploadFile = File(...), language: str = Form("")):
    dados = await file.read()
    audio, _ = sf.read(io.BytesIO(dados))
    texto = FRASES_STT.get(round(len(audio) / 1600), "texto desconhecido")
    registro["stt"].append(texto)
    await asyncio.sleep(0.05)
    return {"text": texto}


@app.post("/v1/audio/speech")
async def tts(request: Request):
    corpo = await request.json()
    registro["tts"].append(corpo["input"])
    await asyncio.sleep(0.05)
    buf = io.BytesIO()
    segundos = float(getattr(app.state, "duracao_tts", 0.1))
    t = np.arange(int(24000 * segundos)) / 24000
    sf.write(buf, (0.05 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 24000, format="WAV")
    return Response(buf.getvalue(), media_type="audio/wav")


@app.get("/openapi.json")
async def esquema():
    import importlib
    import sys
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent / "servicos" / "ferramentas"
    sys.path.insert(0, str(raiz))
    try:
        sys.modules.pop("app", None)
        mod = importlib.import_module("app")
        return mod.app.openapi()
    finally:
        sys.path.remove(str(raiz))
        sys.modules.pop("app", None)


@app.post("/gerar_imagem")
async def gerar_imagem(request: Request):
    registro["ferramentas"].append(("gerar_imagem", await request.json()))
    await asyncio.sleep(float(getattr(app.state, "atraso_imagem", 0.1)))
    return {"url": "http://192.168.0.10:3002/arquivos/imagem-gato.png", "markdown": "![x](y)", "segundos": 0.1}


@app.post("/criar_documento")
async def criar_documento(request: Request):
    registro["ferramentas"].append(("criar_documento", await request.json()))
    return {"url": "http://192.168.0.10:3002/arquivos/lista-abc.pdf", "nome_arquivo": "lista-abc.pdf", "formato": "pdf", "bytes": 10, "markdown": ""}


@app.get("/arquivos/{nome}")
async def arquivo(nome: str):
    return Response(b"%PDF-1.4 teste", media_type="application/pdf")


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServidorEmThread:
    def __init__(self) -> None:
        self.porta = porta_livre()
        self.servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.porta, log_level="warning"))
        self.thread = threading.Thread(target=self.servidor.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.servidor.started:
                break
            time.sleep(0.05)
        return self

    def __exit__(self, *a):
        self.servidor.should_exit = True
        self.thread.join(timeout=5)
