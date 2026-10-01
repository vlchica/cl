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


def _sse(pedacos: list[dict], atraso: float = 0.0, stream: bool = True):
    if not stream:
        # Resposta única (chat.completion) juntando os pedaços
        texto = "".join(p["choices"][0]["delta"].get("content") or "" for p in pedacos)
        chamadas = [tc for p in pedacos for tc in p["choices"][0]["delta"].get("tool_calls") or []]
        msg = {"role": "assistant", "content": texto}
        if chamadas:
            msg["tool_calls"] = [{k: v for k, v in tc.items() if k != "index"} for tc in chamadas]
        return JSONResponse({"id": "sim", "object": "chat.completion", "model": "sim", "choices": [
            {"index": 0, "message": msg, "finish_reason": "tool_calls" if chamadas else "stop"}]})

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


@app.get("/v1/models")
async def modelos():
    return {"object": "list", "data": [{"id": "qwen3-omni", "object": "model", "owned_by": "sim"}, {"id": "teste", "object": "model", "owned_by": "sim"}]}


@app.post("/v1/chat/completions")
async def llm(request: Request):
    corpo = await request.json()
    registro["llm"].append(corpo)
    msgs = corpo["messages"]
    stream = corpo.get("stream", False)
    ferramentas = {t["function"]["name"] for t in corpo.get("tools") or []}

    def texto_de(m):
        c = m.get("content")
        return c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))

    ultima_usuario = next(texto_de(m) for m in reversed(msgs) if m["role"] == "user").lower()
    ja_usou_ferramenta = msgs[-1]["role"] == "tool"
    if corpo["model"] == "quebrado":
        return JSONResponse({"error": "out of memory"}, status_code=500)
    if "imagem" in ultima_usuario and not ja_usou_ferramenta:
        nome = "gerar_imagem" if "gerar_imagem" in ferramentas else "generate_image"
        args = {"descricao_em_ingles": "a cat astronaut", "formato": "quadrado"} if nome == "gerar_imagem" else {"prompt": "a cat astronaut"}
        return _sse([_chamada(nome, args)], stream=stream)
    if "documento" in ultima_usuario and not ja_usou_ferramenta:
        return _sse([_chamada("criar_documento", {"titulo": "Lista", "conteudo_markdown": "- pão\n- café com açúcar", "formato": "pdf"})], stream=stream)
    if "pesquise" in ultima_usuario and not ja_usou_ferramenta:
        if "buscar_web" in ferramentas:
            return _sse([_chamada("buscar_web", {"consulta": "cotação do dólar hoje"})], stream=stream)
        if "search_web" in ferramentas:
            return _sse([_chamada("search_web", {"query": "cotação do dólar hoje"})], stream=stream)
    if ja_usou_ferramenta:
        return _sse([_texto("Pronto! "), _texto("Já está na tela.")], stream=stream)
    if "longa" in ultima_usuario:
        frases = [f"Esta é a frase número {i} de uma resposta bem longa. " for i in range(1, 30)]
        return _sse([_texto(f) for f in frases], atraso=0.15, stream=stream)
    if "pense" in ultima_usuario:
        return _sse([_texto("<thi"), _texto("nk>raciocínio interno</think>"), _texto("Resposta sem pensamento.")], stream=stream)
    return _sse([_texto("Olá! "), _texto("Brasília é a capital do Brasil. "), _texto("Posso ajudar em algo mais?")], stream=stream)


@app.post("/v1/audio/transcriptions")
async def stt(file: UploadFile = File(...), language: str = Form("")):
    dados = await file.read()
    audio, taxa = sf.read(io.BytesIO(dados))
    if len(audio) / taxa >= 0.5:
        texto = "Olá! Qual é a capital do Brasil?"  # fala "de verdade" (teste de ponta a ponta, navegador)
    else:
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


# ---- ComfyUI e SearXNG falsos (para o ensaio com o serviço de ferramentas real)
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005fe02fea7d6a4"
    "0d0000000049454e44ae426082"
)


@app.post("/prompt")
async def comfy_prompt(request: Request):
    registro.setdefault("comfy", []).append(await request.json())
    return {"prompt_id": "p1"}


@app.get("/history/{pid}")
async def comfy_historico(pid: str):
    return {pid: {"status": {"status_str": "success"}, "outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}}}}


@app.get("/view")
async def comfy_ver():
    return Response(PNG_1PX, media_type="image/png")


@app.post("/free")
async def comfy_liberar():
    return {}


@app.get("/system_stats")
async def comfy_estado():
    return {"devices": [{"name": "cpu", "type": "cpu", "vram_total": 0, "vram_free": 0, "torch_vram_total": 0, "torch_vram_free": 0}]}


@app.get("/search")
async def searxng(q: str = "", format: str = "html"):
    registro.setdefault("busca", []).append(q)
    return {"query": q, "results": [
        {"title": "Dólar hoje", "url": "https://exemplo.com/dolar", "content": "O dólar fechou a R$ 5,10."},
        {"title": "Câmbio", "url": "https://exemplo.com/cambio", "content": "Cotação comercial do dia."},
    ], "answers": [], "infoboxes": []}


@app.get("/healthz")
async def searxng_saude():
    return Response("OK")


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
