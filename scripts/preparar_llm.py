"""Baixa e testa os modelos de linguagem, com alternativas automáticas.

Ordem de tentativas para o modelo principal:
  1. Ollama com o GGUF de 4 bits;
  2. se o Ollama não carregar o Qwen3-Omni (arquitetura ainda não suportada nele),
     o mesmo arquivo GGUF no llama.cpp server (sem baixar de novo);
  3. se nada funcionar, o modelo de reserva vira o principal.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.request

from comum import _abridor, atualizar_env, compose, esperar, exec_servico, http, log

PERGUNTA = "Qual é a capital do Brasil? Responda só com o nome da cidade. /no_think"


def _normalizar(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def _resposta_ok(texto: str) -> bool:
    texto = re.sub(r"<think>.*?</think>", "", texto or "", flags=re.S)
    return "brasilia" in _normalizar(texto)


def url_ollama(env: dict) -> str:
    return f"http://localhost:{env.get('PORTA_OLLAMA', '11434')}"


def esperar_ollama(env: dict) -> bool:
    return esperar(lambda: http("GET", f"{url_ollama(env)}/api/version")[0] == 200, 180, "o Ollama iniciar")


def modelo_presente(env: dict, modelo: str) -> bool:
    status, r = http("GET", f"{url_ollama(env)}/api/tags")
    nomes = {m.get("name") for m in (r or {}).get("models", [])} if isinstance(r, dict) else set()
    return modelo in nomes or f"{modelo}:latest" in nomes


def puxar(env: dict, modelo: str) -> bool:
    if modelo_presente(env, modelo):
        log(f"{modelo} já está baixado")
        return True
    log(f"Baixando {modelo} (pode levar vários minutos)…")
    req = urllib.request.Request(
        f"{url_ollama(env)}/api/pull", data=json.dumps({"model": modelo, "stream": True}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    for tentativa in range(1, 4):
        ultimo_pct = -10
        try:
            with _abridor.open(req, timeout=900) as r:
                for linha in r:
                    if not linha.strip():
                        continue
                    d = json.loads(linha)
                    if d.get("error"):
                        raise RuntimeError(d["error"])
                    total, feito = d.get("total"), d.get("completed")
                    if total and feito:
                        pct = int(feito * 100 / total)
                        if pct >= ultimo_pct + 10:
                            log(f"  {modelo}: {pct}% de {total / 1024**3:.1f} GB")
                            ultimo_pct = pct
                    if d.get("status") == "success":
                        log(f"{modelo} baixado", "ok")
                        return True
        except Exception as e:  # noqa: BLE001
            log(f"Falha ao baixar {modelo} (tentativa {tentativa}/3): {e}", "aviso")
            time.sleep(10 * tentativa)
    return modelo_presente(env, modelo)


def testar_ollama(env: dict, modelo: str) -> tuple[bool, str, float]:
    t0 = time.time()
    status, r = http("POST", f"{url_ollama(env)}/api/chat", {
        "model": modelo, "stream": False, "keep_alive": "24h",
        "messages": [{"role": "user", "content": PERGUNTA}],
        "options": {"num_predict": 48, "temperature": 0},
    }, timeout=1800)
    segundos = time.time() - t0
    if status != 200 or not isinstance(r, dict):
        return False, str(r)[:500], segundos
    texto = (r.get("message") or {}).get("content", "")
    return _resposta_ok(texto), texto.strip()[:200], segundos


def blob_gguf(env: dict, modelo: str) -> str:
    status, r = http("POST", f"{url_ollama(env)}/api/show", {"model": modelo})
    if status == 200 and isinstance(r, dict):
        m = re.search(r"^FROM\s+(/\S+sha256-[0-9a-f]+)", r.get("modelfile", ""), re.M)
        if m:
            return m.group(1)
    return ""


def testar_llamacpp() -> tuple[bool, str, float]:
    corpo = json.dumps({"model": "qwen3-omni", "messages": [{"role": "user", "content": PERGUNTA}], "max_tokens": 48, "temperature": 0})
    t0 = time.time()
    saida = exec_servico("llamacpp", "curl", "-s", "-X", "POST", "http://localhost:8080/v1/chat/completions",
                         "-H", "Content-Type: application/json", "-d", corpo, timeout=1800)
    try:
        texto = json.loads(saida)["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return False, saida[:300], time.time() - t0
    return _resposta_ok(texto), texto.strip()[:200], time.time() - t0


def preparar(env: dict, avisos: list[str]) -> dict:
    """Garante um LLM funcionando e grava no .env qual motor e modelo ficaram ativos."""
    compose("up", "-d", "ollama")
    if not esperar_ollama(env):
        raise RuntimeError("o Ollama não iniciou")
    principal, reserva = env["LLM_PRINCIPAL"], env["LLM_RESERVA"]
    resultado = {"principal": principal, "reserva": reserva}

    # Reserva primeiro: é pequena e garante que sempre haverá um modelo funcionando
    if puxar(env, reserva):
        ok, txt, s = testar_ollama(env, reserva)
        resultado["teste_reserva"] = {"ok": ok, "resposta": txt, "segundos": round(s, 1)}
        log(f"Reserva {reserva}: {'OK' if ok else 'FALHOU'} em {s:.1f} s → {txt!r}", "ok" if ok else "aviso")
    else:
        avisos.append(f"Não consegui baixar o modelo de reserva {reserva}.")

    ativo = None
    if puxar(env, principal):
        ok, txt, s = testar_ollama(env, principal)
        resultado["teste_principal_ollama"] = {"ok": ok, "resposta": txt, "segundos": round(s, 1)}
        log(f"Principal {principal} no Ollama: {'OK' if ok else 'FALHOU'} em {s:.1f} s → {txt!r}", "ok" if ok else "aviso")
        if ok:
            ativo = ("ollama", principal)
        elif "Qwen3-Omni" in principal:
            log("O Ollama não rodou o Qwen3-Omni; tentando o mesmo arquivo no llama.cpp…", "aviso")
            arquivo = blob_gguf(env, principal)
            # descarrega o que o Ollama tiver na VRAM antes de subir o llama.cpp
            http("POST", f"{url_ollama(env)}/api/generate", {"model": reserva, "keep_alive": 0})
            env = atualizar_env(LLAMACPP_ARQUIVO=arquivo, COMPOSE_PROFILES=_perfis(env, llamacpp=True))
            compose("up", "-d", "llamacpp")
            if esperar(lambda: '"ok"' in exec_servico("llamacpp", "curl", "-s", "http://localhost:8080/health"), 3600, "o llama.cpp carregar o Qwen3-Omni", 10):
                ok, txt, s = testar_llamacpp()
                resultado["teste_principal_llamacpp"] = {"ok": ok, "resposta": txt, "segundos": round(s, 1)}
                log(f"Qwen3-Omni no llama.cpp: {'OK' if ok else 'FALHOU'} em {s:.1f} s → {txt!r}", "ok" if ok else "aviso")
                if ok:
                    ativo = ("llamacpp", "qwen3-omni")
            if not ativo:
                compose("stop", "llamacpp", verificar=False)
                env = atualizar_env(COMPOSE_PROFILES=_perfis(env, llamacpp=False))
    else:
        avisos.append(f"Não consegui baixar {principal}.")

    if ativo is None:
        if resultado.get("teste_reserva", {}).get("ok"):
            ativo = ("ollama", reserva)
            avisos.append(f"O modelo principal ({principal}) não funcionou; o assistente está usando a reserva {reserva}.")
        else:
            raise RuntimeError("nenhum modelo de linguagem funcionou (principal e reserva falharam)")

    backend, modelo = ativo
    env = atualizar_env(
        LLM_BACKEND=backend,
        LLM_MODELO_ATIVO=modelo,
        USAR_LLAMACPP="true" if backend == "llamacpp" else "false",
        LLM_URL_VOZ="http://llamacpp:8080/v1" if backend == "llamacpp" else "http://ollama:11434/v1",
        COMPOSE_PROFILES=_perfis(env, llamacpp=backend == "llamacpp"),
    )
    resultado.update({"backend": backend, "modelo_ativo": modelo})
    log(f"LLM ativo: {modelo} via {backend}", "ok")
    return resultado


def _perfis(env: dict, llamacpp: bool) -> str:
    perfis = [p for p in env.get("COMPOSE_PROFILES", "").split(",") if p and p != "llamacpp"]
    if llamacpp:
        perfis.append("llamacpp")
    return ",".join(perfis)
