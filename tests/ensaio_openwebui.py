#!/usr/bin/env python3
"""Ensaio da instalação sem GPU e sem baixar modelos (ferramenta de desenvolvimento).

Sobe o Open WebUI real (pip install open-webui==0.11.4) com exatamente as variáveis
do docker-compose.yml, os serviços "ferramentas" e "voz" reais, e simuladores no
lugar do LLM, do Whisper/Kokoro, do ComfyUI e do SearXNG. Os nomes do compose
(fala, ferramentas, searxng...) apontam para IPs de loopback em /etc/hosts.

Depois roda scripts/configurar_openwebui.py duas vezes (a segunda não deve mudar
nada) e scripts/teste_ponta_a_ponta.py inteiro.

Precisa de root (edita /etc/hosts) e do Docker Compose (só para ler a configuração):
    sudo python3 tests/ensaio_openwebui.py --open-webui /caminho/venv/bin/open-webui [--manter]
O Python que roda este script precisa dos pacotes de servicos/ferramentas e servicos/voz.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MARCA = "# ensaio-assistente"
HOSTS = {"fala": "127.0.0.2", "ferramentas": "127.0.0.3", "searxng": "127.0.0.4", "comfyui": "127.0.0.5",
         "llamacpp": "127.0.0.6", "ollama": "127.0.0.7"}


def editar_hosts(adicionar: bool) -> None:
    linhas = [l for l in Path("/etc/hosts").read_text().splitlines() if MARCA not in l]
    if adicionar:
        linhas += [f"{ip} {nome}  {MARCA}" for nome, ip in HOSTS.items()]
    Path("/etc/hosts").write_text("\n".join(linhas) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--open-webui", default=shutil.which("open-webui"), help="executável do open-webui")
    ap.add_argument("--manter", action="store_true", help="deixa tudo no ar no fim (Open WebUI em :13000)")
    a = ap.parse_args()
    if not a.open_webui:
        print("Instale: pip install open-webui==0.11.4 (num venv) e passe --open-webui")
        return 2

    pasta = Path(tempfile.mkdtemp(prefix="ensaio-assistente-"))
    projeto = pasta / "projeto"
    shutil.copytree(REPO, projeto, ignore=shutil.ignore_patterns(".git", "node_modules", "logs", "estado", ".env"))
    subprocess.run([sys.executable, "scripts/detectar_hardware.py"], cwd=projeto, check=True, capture_output=True)
    sys.path.insert(0, str(projeto / "scripts"))
    import comum

    comum.RAIZ, comum.ARQ_ENV = projeto, projeto / ".env"
    comum.atualizar_env(
        USAR_LLAMACPP="true", LLM_BACKEND="llamacpp", LLM_MODELO_ATIVO="qwen3-omni", LLM_URL_VOZ="http://llamacpp:8080/v1",
        HOST_IP="127.0.0.1", PORTA_WEBUI="13000", PORTA_VOZ="3001", PORTA_ARQUIVOS="3002", IMAGENS_ATIVAS="true",
        SD_MODELO="sd15", COMPOSE_PROFILES="imagens,llamacpp", OPENWEBUI_API_KEY="",
    )
    cfg = json.loads(subprocess.run(["docker", "compose", "config", "--format", "json"], cwd=projeto, check=True,
                                    capture_output=True, text=True).stdout)
    amb = {n: {k: str(v) for k, v in (cfg["services"][n].get("environment") or {}).items()} for n in ("open-webui", "voz", "ferramentas")}

    editar_hosts(True)
    sys.path.insert(0, str(REPO / "tests"))
    import simuladores
    import uvicorn

    simuladores.app.state.duracao_tts = 1.5  # áudio "real": o STT simulado devolve a pergunta da capital
    servidores = []
    for ip, porta in ((HOSTS["fala"], 8000), (HOSTS["searxng"], 8080), (HOSTS["comfyui"], 8188), (HOSTS["llamacpp"], 8080)):
        srv = uvicorn.Server(uvicorn.Config(simuladores.app, host=ip, port=porta, log_level="warning"))
        threading.Thread(target=srv.run, daemon=True).start()
        servidores.append(srv)

    sem_proxy = ",".join(["localhost", "127.0.0.1", *HOSTS])
    base_env = {**os.environ, "NO_PROXY": sem_proxy, "no_proxy": sem_proxy}
    arquivos = pasta / "arquivos"
    arquivos.mkdir()
    procs = []

    def iniciar(cmd, extra, log):
        procs.append(subprocess.Popen(cmd, env={**base_env, **extra}, stdout=open(pasta / log, "w"), stderr=subprocess.STDOUT))

    ferr = {**amb["ferramentas"], "ARQUIVOS_DIR": str(arquivos)}
    uv = [sys.executable, "-m", "uvicorn", "app:app", "--log-level", "warning"]
    iniciar(uv + ["--app-dir", str(REPO / "servicos/ferramentas"), "--host", HOSTS["ferramentas"], "--port", "8000"], ferr, "ferramentas.log")
    iniciar(uv + ["--app-dir", str(REPO / "servicos/ferramentas"), "--host", "127.0.0.1", "--port", "3002"], ferr, "ferramentas-host.log")
    iniciar(uv + ["--app-dir", str(REPO / "servicos/voz"), "--host", "127.0.0.1", "--port", "3001"],
            {**amb["voz"], "CONVERSAS_DIR": str(pasta / "conversas")}, "voz.log")
    iniciar([a.open_webui, "serve", "--host", "127.0.0.1", "--port", "13000"],
            {**amb["open-webui"], "DATA_DIR": str(pasta / "open-webui"), "OFFLINE_MODE": "true", "HF_HUB_OFFLINE": "1",
             "ENABLE_OLLAMA_API": "false"}, "open-webui.log")
    codigo = 1
    try:
        if not comum.esperar(lambda: comum.http("GET", "http://localhost:13000/health")[0] == 200, 600, "Open WebUI", 3):
            print("Open WebUI não subiu; veja", pasta / "open-webui.log")
            return 1
        import configurar_openwebui
        import teste_ponta_a_ponta

        r1 = configurar_openwebui.configurar(comum.ler_env())
        print("Configurador (1ª vez):", json.dumps(r1["ajustes"], ensure_ascii=False), "| modelos:", r1["modelos"])
        r2 = configurar_openwebui.configurar(comum.ler_env())
        print("Configurador (2ª vez, deve ser {}):", r2["ajustes"])
        teste_ponta_a_ponta.RAIZ = projeto
        t = teste_ponta_a_ponta.executar(comum.ler_env())
        print(t.relatorio())
        sistema = [m["content"][:60] for c in simuladores.registro["llm"] for m in c["messages"] if m["role"] == "system"]
        print("Prompt de sistema recebido pelo LLM via Open WebUI:", sistema[:1])
        codigo = 0 if all(x["ok"] for x in t.resultados) and not r2["ajustes"] else 1
        if a.manter:
            print(f"No ar: Open WebUI http://localhost:13000 (senha em {projeto / '.env'}), voz http://localhost:3001. Ctrl+C para sair.")
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()
        for s in servidores:
            s.should_exit = True
        editar_hosts(False)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
