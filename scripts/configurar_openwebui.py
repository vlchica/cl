#!/usr/bin/env python3
"""Configura o Open WebUI já em execução: entra como administrador, cria a chave de
API e confere (e corrige, se preciso) voz, imagens, busca, LLM e ferramentas.

As variáveis de ambiente do docker-compose só valem no primeiro início do Open WebUI;
depois ele guarda a configuração no banco. Este script garante que uma reinstalação
(com outro modelo, outra voz, outro motor) também atualize o que já estava salvo.
"""
from __future__ import annotations

import sys

from comum import atualizar_env, esperar, http, ler_env, log


def _base(env: dict) -> str:
    return f"http://localhost:{env.get('PORTA_WEBUI', '3000')}"


def entrar(env: dict) -> str:
    base = _base(env)
    if not esperar(lambda: http("GET", f"{base}/health")[0] == 200, 600, "o Open WebUI iniciar"):
        raise RuntimeError("o Open WebUI não respondeu em 10 minutos")
    status, r = http("POST", f"{base}/api/v1/auths/signin", {"email": env["ADMIN_EMAIL"], "password": env["ADMIN_SENHA"]})
    if status != 200 or not isinstance(r, dict) or "token" not in r:
        raise RuntimeError(f"não consegui entrar como administrador ({status}): {r}")
    return r["token"]


def garantir_chave_api(env: dict, token: str) -> str:
    base = _base(env)
    chave = env.get("OPENWEBUI_API_KEY", "")
    if chave and http("GET", f"{base}/api/models", cabecalhos={"Authorization": f"Bearer {chave}"})[0] == 200:
        return chave
    status, r = http("POST", f"{base}/api/v1/auths/api_key", cabecalhos={"Authorization": f"Bearer {token}"})
    if status != 200:
        raise RuntimeError(f"não consegui criar a chave de API ({status}): {r}")
    atualizar_env(OPENWEBUI_API_KEY=r["api_key"])
    return r["api_key"]


def _ajustar(base: str, token: str, get: str, post: str, desejado: dict, envelope: str | None = None) -> list[str]:
    """Lê a configuração, compara com o desejado e grava só se algo mudou."""
    cab = {"Authorization": f"Bearer {token}"}
    status, atual = http("GET", f"{base}{get}", cabecalhos=cab)
    if status != 200 or not isinstance(atual, dict):
        return [f"não consegui ler {get} ({status})"]
    alvo = atual.get(envelope, {}) if envelope else atual
    mudancas = []
    for caminho, valor in desejado.items():
        no = alvo
        partes = caminho.split(".")
        for p in partes[:-1]:
            no = no.setdefault(p, {})
        if no.get(partes[-1]) != valor:
            mudancas.append(f"{caminho}: {no.get(partes[-1])!r} → {valor!r}")
            no[partes[-1]] = valor
    if mudancas:
        corpo = {envelope: alvo} if envelope else alvo
        status, r = http("POST", f"{base}{post}", corpo, cabecalhos=cab)
        if status != 200:
            return [f"falha ao gravar {post} ({status}): {str(r)[:300]}"]
    return mudancas


def configurar(env: dict | None = None) -> dict:
    env = env or ler_env()
    base = _base(env)
    token = entrar(env)
    chave = garantir_chave_api(env, token)
    usar_llamacpp = env.get("USAR_LLAMACPP") == "true"
    imagens = env.get("IMAGENS_ATIVAS", "true") == "true"
    ajustes: dict[str, list[str]] = {}

    ajustes["voz"] = _ajustar(base, token, "/api/v1/audio/config", "/api/v1/audio/config/update", {
        "stt.ENGINE": "openai",
        "stt.OPENAI_API_BASE_URL": "http://fala:8000/v1",
        "stt.OPENAI_API_KEY": "local",
        "stt.MODEL": env.get("WHISPER_MODELO", "large-v3-turbo"),
        "tts.ENGINE": "openai",
        "tts.OPENAI_API_BASE_URL": "http://fala:8000/v1",
        "tts.OPENAI_API_KEY": "local",
        "tts.MODEL": "kokoro",
        "tts.VOICE": env.get("TTS_VOZ", "pf_dora"),
        "tts.SPLIT_ON": "punctuation",
    })
    ajustes["imagens"] = _ajustar(base, token, "/api/v1/images/config", "/api/v1/images/config/update", {
        "ENABLE_IMAGE_GENERATION": imagens,
        "IMAGE_GENERATION_ENGINE": "openai",
        "IMAGES_OPENAI_API_BASE_URL": "http://ferramentas:8000/v1",
        "IMAGES_OPENAI_API_KEY": env.get("FERRAMENTAS_TOKEN", ""),
        "IMAGE_GENERATION_MODEL": env.get("SD_MODELO", "sd15"),
        "IMAGE_SIZE": env.get("IMAGEM_TAMANHO", "512x512"),
        "IMAGE_STEPS": int(env.get("IMAGEM_PASSOS", "25")),
    })
    ajustes["busca"] = _ajustar(base, token, "/api/v1/retrieval/config", "/api/v1/retrieval/config/update", {
        "ENABLE_WEB_SEARCH": True,
        "WEB_SEARCH_ENGINE": "searxng",
        "SEARXNG_QUERY_URL": "http://searxng:8080/search?q=<query>",
        "SEARXNG_LANGUAGE": "pt-BR",
    }, envelope="web")
    ajustes["llm"] = _ajustar(base, token, "/openai/config", "/openai/config/update", {
        "ENABLE_OPENAI_API": usar_llamacpp,
        "OPENAI_API_BASE_URLS": ["http://llamacpp:8080/v1"],
        "OPENAI_API_KEYS": ["local"],
    })
    ajustes["modelo_padrao"] = _ajustar(base, token, "/api/v1/configs/models", "/api/v1/configs/models", {
        "DEFAULT_MODELS": env.get("LLM_MODELO_ATIVO", ""),
    })
    conexao = {
        "type": "openapi", "url": "http://ferramentas:8000", "path": "openapi.json", "auth_type": "bearer",
        "key": env.get("FERRAMENTAS_TOKEN", ""),
        "config": {"enable": True, "function_name_filter_list": "criar_documento"},
        "info": {"id": "ferramentas", "name": "Documentos", "description": "Cria arquivos PDF, Word, TXT e Markdown para download"},
    }
    cab = {"Authorization": f"Bearer {token}"}
    status, atual = http("GET", f"{base}/api/v1/configs/tool_servers", cabecalhos=cab)
    lista = (atual or {}).get("TOOL_SERVER_CONNECTIONS", []) if isinstance(atual, dict) else []
    outras = [c for c in lista if (c.get("info") or {}).get("id") != "ferramentas"]
    nossa = next((c for c in lista if (c.get("info") or {}).get("id") == "ferramentas"), None)
    if nossa != conexao:
        status, r = http("POST", f"{base}/api/v1/configs/tool_servers", {"TOOL_SERVER_CONNECTIONS": outras + [conexao]}, cabecalhos=cab)
        ajustes["ferramentas"] = ["conexão do servidor de ferramentas atualizada" if status == 200 else f"falha ({status}): {str(r)[:200]}"]

    # O modelo ativo aparece na lista do Open WebUI?
    status, modelos = http("GET", f"{base}/api/models", cabecalhos={"Authorization": f"Bearer {chave}"}, timeout=60)
    ids = [m.get("id") for m in (modelos or {}).get("data", [])] if isinstance(modelos, dict) else []
    return {"chave_api": chave, "ajustes": {k: v for k, v in ajustes.items() if v}, "modelos": ids}


def main() -> int:
    try:
        r = configurar()
    except Exception as e:  # noqa: BLE001
        log(str(e), "erro")
        return 1
    for area, mudancas in r["ajustes"].items():
        for m in mudancas:
            log(f"{area}: {m}")
    log(f"Modelos disponíveis no Open WebUI: {', '.join(r['modelos']) or 'nenhum'}", "ok" if r["modelos"] else "aviso")
    return 0


if __name__ == "__main__":
    sys.exit(main())
