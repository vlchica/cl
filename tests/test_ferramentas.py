"""Testes do servidor de ferramentas com SearXNG, ComfyUI e Ollama simulados."""
from __future__ import annotations

import base64
import importlib
import io
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent
TOKEN = "segredo-de-teste"
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class Simulador:
    """Responde como SearXNG, ComfyUI e Ollama."""

    def __init__(self) -> None:
        self.chamadas: list[str] = []
        self.fluxos: list[dict] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        self.chamadas.append(f"{req.method} {req.url.path}")
        if "/search" in url:
            assert req.url.params["format"] == "json"
            return httpx.Response(200, json={
                "results": [
                    {"title": "Previsão do tempo", "url": "https://exemplo.com/a", "content": "Sol com nuvens, 28 °C"},
                    {"title": "Duplicado", "url": "https://exemplo.com/a", "content": "x"},
                    {"title": "Outro", "url": "https://exemplo.com/b", "content": "Chuva à tarde"},
                ],
                "answers": [],
                "infoboxes": [],
            })
        if req.url.path == "/prompt":
            self.fluxos.append(json.loads(req.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": "p1"})
        if req.url.path == "/history/p1":
            return httpx.Response(200, json={"p1": {"status": {"status_str": "success"}, "outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}}}})
        if req.url.path == "/view":
            return httpx.Response(200, content=PNG_1PX, headers={"content-type": "image/png"})
        if req.url.path == "/free":
            return httpx.Response(200, json={})
        if req.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        if req.url.path == "/api/generate":
            assert json.loads(req.content)["keep_alive"] == 0
            return httpx.Response(200, json={"done": True})
        return httpx.Response(404, json={"erro": url})


@pytest.fixture()
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("FERRAMENTAS_TOKEN", TOKEN)
    monkeypatch.setenv("ARQUIVOS_DIR", str(tmp_path))
    monkeypatch.setenv("ARQUIVOS_URL_PUBLICA", "http://192.168.0.10:3002")
    monkeypatch.setenv("LIBERAR_VRAM_PARA_IMAGEM", "1")
    monkeypatch.setenv("SD_MODELO", "sdxl")
    sys.path.insert(0, str(RAIZ / "servicos" / "ferramentas"))
    for m in ("app", "documentos"):
        sys.modules.pop(m, None)
    app_mod = importlib.import_module("app")
    sim = Simulador()
    original = httpx.AsyncClient

    def cliente_falso(*a, **kw):
        kw["transport"] = httpx.MockTransport(sim)
        return original(*a, **kw)

    monkeypatch.setattr(app_mod.httpx, "AsyncClient", cliente_falso)
    c = TestClient(app_mod.app)
    c.headers["Authorization"] = f"Bearer {TOKEN}"
    c.sim = sim  # type: ignore[attr-defined]
    c.pasta = tmp_path  # type: ignore[attr-defined]
    yield c
    sys.path.remove(str(RAIZ / "servicos" / "ferramentas"))
    for m in ("app", "documentos"):
        sys.modules.pop(m, None)


def test_esquema_expoe_so_as_ferramentas(cliente):
    esquema = cliente.get("/openapi.json").json()
    ids = {op["operationId"] for caminho in esquema["paths"].values() for op in caminho.values()}
    assert ids == {"buscar_web", "ler_pagina", "gerar_imagem", "criar_documento", "data_hora"}


def test_exige_token(cliente):
    r = httpx.post  # noqa: F841
    sem = TestClient(cliente.app)
    assert sem.post("/criar_documento", json={"titulo": "x", "conteudo_markdown": "y"}).status_code == 401


def test_busca_remove_duplicados(cliente):
    r = cliente.post("/buscar_web", json={"consulta": "tempo em São Paulo", "quantidade": 5})
    assert r.status_code == 200, r.text
    d = r.json()
    assert [x["url"] for x in d["resultados"]] == ["https://exemplo.com/a", "https://exemplo.com/b"]


MD = """# Relatório de ação

Texto com **negrito**, *itálico*, `código` e acentuação: ção, ã, é, ü — “aspas”.

## Lista
- maçã
- pão de queijo

1. primeiro
2. segundo

| Item | Preço |
|------|-------|
| Café | R$ 10,50 |

> Uma citação.

```
print("olá")
```
"""


@pytest.mark.parametrize("formato", ["pdf", "docx", "txt", "md"])
def test_cria_documentos(cliente, formato):
    r = cliente.post("/criar_documento", json={"titulo": "Relatório de ação", "conteudo_markdown": MD, "formato": formato})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["url"].startswith("http://192.168.0.10:3002/arquivos/relatorio-de-acao-")
    arquivo = cliente.pasta / d["nome_arquivo"]
    assert arquivo.exists() and arquivo.stat().st_size > 100
    baixado = cliente.get(f"/arquivos/{d['nome_arquivo']}")
    assert baixado.status_code == 200
    # Lê de volta pelo extrator usado no modo de voz
    ext = cliente.post("/extrair_texto", files={"arquivo": (d["nome_arquivo"], baixado.content)})
    assert ext.status_code == 200, ext.text
    texto = ext.json()["texto"]
    if formato == "pdf":
        assert baixado.content.startswith(b"%PDF")
    for trecho in ("Relatório", "maçã", "pão de queijo", "Café"):
        assert trecho in texto, (formato, trecho, texto[:500])


def test_gera_imagem_liberando_vram(cliente):
    r = cliente.post("/gerar_imagem", json={"descricao_em_ingles": "a cat astronaut, digital art", "formato": "paisagem"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["markdown"].startswith("![a cat astronaut")
    assert (cliente.pasta / d["url"].rsplit("/", 1)[1]).read_bytes() == PNG_1PX
    chamadas = cliente.sim.chamadas
    # Descarrega o LLM antes e libera o ComfyUI depois
    assert chamadas.index("GET /api/ps") < chamadas.index("POST /prompt") < chamadas.index("POST /free")
    fluxo = cliente.sim.fluxos[0]
    assert fluxo["4"]["inputs"]["ckpt_name"] == "sd_xl_base_1.0.safetensors"
    assert (fluxo["5"]["inputs"]["width"], fluxo["5"]["inputs"]["height"]) == (1216, 832)


def test_api_de_imagens_formato_openai(cliente):
    r = cliente.post("/v1/images/generations", json={"model": "sdxl", "prompt": "a red apple", "n": 1, "size": "512x512", "response_format": "b64_json"})
    assert r.status_code == 200, r.text
    assert base64.b64decode(r.json()["data"][0]["b64_json"]) == PNG_1PX
    fluxo = cliente.sim.fluxos[0]
    assert (fluxo["5"]["inputs"]["width"], fluxo["5"]["inputs"]["height"]) == (1024, 1024)


def test_data_hora_em_portugues(cliente):
    d = cliente.get("/data_hora").json()
    assert any(dia in d["data_hora"] for dia in ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"))
    assert d["fuso"] == "America/Sao_Paulo"


def test_extrai_texto_simples(cliente):
    r = cliente.post("/extrair_texto", files={"arquivo": ("nota.txt", io.BytesIO("Lembrete: comprar café".encode()))})
    assert r.json()["texto"] == "Lembrete: comprar café"
