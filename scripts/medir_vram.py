#!/usr/bin/env python3
"""Mede quanta VRAM cada componente do assistente está usando.

No Linux usa o nvidia-smi por processo e descobre a qual contêiner cada processo
pertence. No Windows/WSL o nvidia-smi não informa memória por processo; aí os
valores vêm de cada serviço (Ollama, llama.cpp, ComfyUI, serviço de fala).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from comum import compose, exec_servico, http, ler_env

NOMES = {
    "ollama": "LLM (Ollama)",
    "llamacpp": "LLM (llama.cpp)",
    "fala": "Whisper + Kokoro (fala)",
    "comfyui": "Stable Diffusion (ComfyUI)",
    "open-webui": "Open WebUI",
    "voz": "Modo de voz",
    "ferramentas": "Ferramentas",
}


def _smi(*args: str) -> str:
    exe = shutil.which("nvidia-smi") or ("nvidia-smi.exe" if shutil.which("nvidia-smi.exe") else None)
    if not exe:
        return ""
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def gpus() -> list[dict]:
    saida = _smi("--query-gpu=index,name,memory.total,memory.used", "--format=csv,noheader,nounits")
    lista = []
    for linha in saida.strip().splitlines():
        p = [x.strip() for x in linha.split(",")]
        if len(p) >= 4:
            lista.append({"indice": int(p[0]), "nome": p[1], "total_mb": int(float(p[2])), "usada_mb": int(float(p[3]))})
    return lista


def _conteineres() -> dict[str, str]:
    """id completo do contêiner → nome do serviço do compose."""
    r = compose("ps", "--format", "json", verificar=False, capturar=True)
    mapa = {}
    texto = r.stdout.strip()
    itens = json.loads(texto) if texto.startswith("[") else [json.loads(l) for l in texto.splitlines() if l.strip()]
    for c in itens:
        cid = subprocess.run(["docker", "inspect", "-f", "{{.Id}}", c["Name"]], capture_output=True, text=True).stdout.strip()
        if cid:
            mapa[cid] = c.get("Service", c["Name"])
    return mapa


def por_processo() -> dict[str, int]:
    saida = _smi("--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits")
    if not saida.strip() or "Not available" in saida or "[N/A]" in saida:
        return {}
    conteineres = _conteineres()
    uso: dict[str, int] = {}
    for linha in saida.strip().splitlines():
        p = [x.strip() for x in linha.split(",")]
        if len(p) < 2 or not p[1].isdigit():
            continue
        try:
            cgroup = Path(f"/proc/{p[0]}/cgroup").read_text()
        except OSError:
            continue
        servico = next((nome for cid, nome in conteineres.items() if cid in cgroup), None)
        if servico:
            uso[servico] = uso.get(servico, 0) + int(p[1])
    return uso


def pelos_servicos(env: dict) -> dict[str, int]:
    """Plano B: cada serviço informa o próprio uso."""
    uso: dict[str, int] = {}
    status, r = http("GET", f"http://localhost:{env.get('PORTA_OLLAMA', '11434')}/api/ps")
    if status == 200 and isinstance(r, dict):
        total = sum(int(m.get("size_vram", 0)) for m in r.get("models", []))
        if total:
            uso["ollama"] = total // 1024**2
    saude = exec_servico("fala", "curl", "-s", "http://localhost:8000/health")
    try:
        d = json.loads(saude)
        fala = sum(int((d.get(k) or {}).get("vram_mb") or 0) for k in ("stt", "tts"))
        if fala:
            uso["fala"] = fala
    except (json.JSONDecodeError, TypeError):
        pass
    est = exec_servico("comfyui", "curl", "-s", "http://localhost:8188/system_stats")
    try:
        dev = json.loads(est)["devices"][0]
        torch_total = int(dev.get("torch_vram_total", 0)) - int(dev.get("torch_vram_free", 0))
        if torch_total > 0:
            uso["comfyui"] = torch_total // 1024**2
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        pass
    return uso


def medir(env: dict | None = None) -> dict:
    env = env or ler_env()
    placas = gpus()
    if not placas:
        return {"gpus": [], "componentes": {}, "metodo": "sem GPU NVIDIA"}
    uso = por_processo()
    metodo = "nvidia-smi por processo"
    if not uso:
        uso = pelos_servicos(env)
        metodo = "informado por cada serviço (Windows/WSL não expõe uso por processo)"
        total = sum(g["usada_mb"] for g in placas)
        if env.get("LLM_BACKEND") == "llamacpp":
            resto = total - sum(uso.values())
            if resto > 0:
                uso["llamacpp"] = resto
    return {"gpus": placas, "componentes": uso, "metodo": metodo}


def tabela_md(m: dict) -> str:
    if not m["gpus"]:
        return "Sem GPU NVIDIA: todos os componentes usam RAM/CPU.\n"
    linhas = ["| Componente | VRAM |", "|---|---:|"]
    for servico, mb in sorted(m["componentes"].items(), key=lambda x: -x[1]):
        linhas.append(f"| {NOMES.get(servico, servico)} | {mb / 1024:.1f} GB |")
    for g in m["gpus"]:
        linhas.append(f"| **GPU {g['indice']} ({g['nome']}) — total em uso** | **{g['usada_mb'] / 1024:.1f} de {g['total_mb'] / 1024:.0f} GB** |")
    linhas.append(f"\nMedição: {m['metodo']}.")
    return "\n".join(linhas) + "\n"


if __name__ == "__main__":
    resultado = medir()
    if "--json" in sys.argv:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    else:
        print(tabela_md(resultado))
