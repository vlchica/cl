#!/usr/bin/env python3
"""Instalação completa do assistente de voz local (Linux, WSL ou Windows).

Os scripts instalar.sh / instalar.ps1 cuidam de driver NVIDIA, Docker e Python e
depois chamam este arquivo, que faz o resto sem perguntar nada:

  1. levanta o hardware e escolhe os modelos (scripts/detectar_hardware.py)
  2. confere se o Docker enxerga a GPU (senão, segue em modo CPU)
  3. desativa um Ollama nativo que disputaria porta e VRAM
  4. constrói as imagens próprias e baixa as demais
  5. baixa e testa os LLMs (Ollama → llama.cpp → reserva)
  6. sobe fala, imagens, busca, ferramentas, Open WebUI e modo de voz, com alternativas
  7. configura o Open WebUI (admin, chave de API, conexões)
  8. liga o início automático com o computador
  9. roda o teste de ponta a ponta e grava relatorio-instalacao.md

Pode ser executado de novo a qualquer momento: reaproveita o que já está pronto.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import configurar_openwebui  # noqa: E402
import detectar_hardware  # noqa: E402
import preparar_llm  # noqa: E402
from comum import (  # noqa: E402
    RAIZ, atualizar_env, compose, eh_root, eh_windows, esperar, exec_servico, http, iniciar_log, ler_env, log, rodar,
)

avisos: list[str] = []
resultados: dict = {}


# --------------------------------------------------------------------------- etapas
def verificar_docker() -> None:
    if not shutil.which("docker"):
        raise RuntimeError("Docker não encontrado. Rode primeiro instalar.sh (Linux) ou instalar.ps1 (Windows).")
    if not esperar(lambda: rodar(["docker", "info"], verificar=False, capturar=True).returncode == 0, 300, "o Docker iniciar", 5):
        raise RuntimeError("o Docker não está rodando")
    r = rodar(["docker", "compose", "version", "--short"], verificar=False, capturar=True)
    if r.returncode != 0:
        raise RuntimeError("Docker Compose v2 não encontrado (comando 'docker compose').")
    log(f"Docker OK, Compose {r.stdout.strip()}", "ok")


def gpu_no_docker() -> bool:
    r = rodar(["docker", "run", "--rm", "--gpus", "all", "debian:bookworm-slim", "nvidia-smi", "-L"],
              verificar=False, capturar=True, timeout=600)
    if r.returncode == 0 and "GPU" in r.stdout:
        log("O Docker enxerga a GPU: " + r.stdout.strip().replace("\n", " | "), "ok")
        return True
    log(f"O Docker não acessou a GPU: {(r.stderr or r.stdout).strip()[-300:]}", "aviso")
    return False


def detectar(args) -> dict:
    hw = detectar_hardware.detectar()
    atuais = detectar_hardware.ler_env()
    forcar_omni = args.forcar_omni or atuais.get("FORCAR_OMNI") == "1"
    plano = detectar_hardware.planejar(hw, forcar_omni=forcar_omni, sem_imagens=args.sem_imagens, forcar_cpu=args.forcar_cpu)
    portas, av_portas = detectar_hardware.escolher_portas(atuais)
    plano["avisos"] += av_portas
    env = detectar_hardware.montar_env(hw, plano, atuais, portas)
    if forcar_omni:
        env["FORCAR_OMNI"] = "1"
    detectar_hardware.gravar_env(env)
    (RAIZ / "estado").mkdir(exist_ok=True)
    (RAIZ / "estado" / "plano.json").write_text(json.dumps({"hardware": hw, "plano": plano}, ensure_ascii=False, indent=2), encoding="utf-8")
    relatorio = detectar_hardware.relatorio_md(hw, plano)
    (RAIZ / "relatorio-hardware.md").write_text(relatorio, encoding="utf-8")
    print(relatorio)
    avisos.extend(plano["avisos"])
    resultados["hardware"] = hw
    resultados["plano"] = plano
    return hw


def desativar_ollama_nativo() -> None:
    if eh_windows():
        for exe in ("ollama app.exe", "ollama.exe"):
            subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True)
        atalho = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/Ollama.lnk"
        if atalho.exists():
            atalho.rename(atalho.with_suffix(".lnk.desativado"))
        log("Ollama nativo do Windows encerrado e removido da inicialização (os modelos dele continuam no disco)", "ok")
        return
    if shutil.which("systemctl") and subprocess.run(["systemctl", "is-active", "--quiet", "ollama"]).returncode == 0:
        prefixo = [] if eh_root() else ["sudo"]
        subprocess.run([*prefixo, "systemctl", "disable", "--now", "ollama"], check=False)
        log("Serviço ollama nativo desativado (os modelos dele continuam em /usr/share/ollama)", "ok")
    else:
        subprocess.run(["pkill", "-f", "ollama serve"], check=False)


def construir(env: dict) -> None:
    log(f"Construindo a imagem base PyTorch ({env['TORCH_INDICE']}) — na primeira vez leva de 5 a 20 minutos", "passo")
    for tentativa in (1, 2):
        try:
            compose("--profile", "construir", "build", "base")
            break
        except RuntimeError:
            if tentativa == 2:
                raise
            log("Falha ao construir a base; tentando de novo…", "aviso")
    log("Construindo os serviços fala, ferramentas, voz e comfyui", "passo")
    for tentativa in (1, 2):
        try:
            compose("build")
            break
        except RuntimeError:
            if tentativa == 2:
                raise
            log("Falha na construção; tentando de novo…", "aviso")
    log("Baixando as imagens prontas (Open WebUI, Ollama, SearXNG, Caddy)", "passo")
    compose("pull", "--ignore-buildable", verificar=False)


def saude_fala() -> dict:
    try:
        return json.loads(exec_servico("fala", "curl", "-s", "http://localhost:8000/health"))
    except (json.JSONDecodeError, ValueError):
        return {}


def preparar_fala() -> None:
    compose("up", "-d", "fala")
    log("Baixando e carregando Whisper e Kokoro (só demora na primeira vez)…")
    pronto = esperar(lambda: saude_fala().get("status") in ("ok", "erro"), 2700, "o serviço de fala", 5)
    d = saude_fala()
    resultados["fala"] = d
    if not pronto or d.get("status") != "ok":
        raise RuntimeError(f"serviço de fala não ficou pronto: {json.dumps(d, ensure_ascii=False)[:600]}")
    stt, tts = d["stt"], d["tts"]
    log(f"Whisper {stt['modelo']} em {stt['dispositivo']} ({stt['compute_type']}); voz: {tts['motor']} em {tts['dispositivo']}", "ok")
    for falha in stt.get("falhas_anteriores", []) + tts.get("falhas_anteriores", []):
        avisos.append(f"Alternativa usada na voz: {falha[:200]}")
    if tts["motor"] != "kokoro":
        avisos.append("O Kokoro não funcionou; a síntese de voz está usando o Piper (pt_BR-faber-medium).")


def testar_imagem(env: dict) -> bool:
    status, r = http("POST", f"http://localhost:{env['PORTA_ARQUIVOS']}/v1/images/generations",
                     {"prompt": "a red apple on a wooden table, photo", "size": "512x512", "steps": 6},
                     cabecalhos={"Authorization": f"Bearer {env['FERRAMENTAS_TOKEN']}"}, timeout=1800)
    return status == 200 and isinstance(r, dict) and bool(r.get("data"))


def preparar_imagens(env: dict) -> dict:
    if env.get("IMAGENS_ATIVAS") != "true":
        return env
    log("Baixando o Stable Diffusion e testando uma imagem…")
    ok = esperar(lambda: '"devices"' in exec_servico("comfyui", "curl", "-s", "http://localhost:8188/system_stats"), 3600, "o ComfyUI (download do modelo)", 10)
    if ok and testar_imagem(env):
        log(f"Geração de imagens OK ({env['SD_MODELO']})", "ok")
        return env
    if env.get("SD_MODELO") == "sdxl":
        avisos.append("SDXL falhou nesta máquina; trocado por Stable Diffusion 1.5.")
        env = atualizar_env(SD_MODELO="sd15", IMAGEM_TAMANHO="512x512", COMFYUI_ARGS="--lowvram" if env["MODO"] == "gpu" else "--cpu")
        compose("up", "-d", "comfyui", "ferramentas")
        ok = esperar(lambda: '"devices"' in exec_servico("comfyui", "curl", "-s", "http://localhost:8188/system_stats"), 3600, "o ComfyUI com SD 1.5", 10)
        if ok and testar_imagem(env):
            log("Geração de imagens OK (SD 1.5)", "ok")
            return env
    avisos.append("A geração de imagens não funcionou e foi desativada. Veja 'docker compose logs comfyui'.")
    perfis = ",".join(p for p in env.get("COMPOSE_PROFILES", "").split(",") if p and p != "imagens")
    env = atualizar_env(IMAGENS_ATIVAS="false", COMPOSE_PROFILES=perfis)
    compose("stop", "comfyui", verificar=False)
    return env


def subir_tudo(env: dict) -> dict:
    compose("up", "-d", "--remove-orphans")
    base = f"http://localhost:{env['PORTA_WEBUI']}"
    for nome, url, limite in (
        ("Open WebUI", f"{base}/health", 900),
        ("ferramentas", f"http://localhost:{env['PORTA_ARQUIVOS']}/health", 120),
        ("modo de voz", f"http://localhost:{env['PORTA_VOZ']}/health", 120),
    ):
        if esperar(lambda u=url: http("GET", u)[0] == 200, limite, nome):
            log(f"{nome} no ar", "ok")
        else:
            avisos.append(f"{nome} não respondeu a tempo; veja 'docker compose logs'.")
    return preparar_imagens(env)


def instalar_autostart(env: dict) -> str:
    if eh_windows():
        tarefa = "Assistente de voz"
        script = RAIZ / "scripts" / "iniciar.ps1"
        cmd = f'powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{script}"'
        r = subprocess.run(["schtasks", "/Create", "/TN", tarefa, "/SC", "ONLOGON", "/DELAY", "0000:30", "/RL", "HIGHEST", "/TR", cmd, "/F"],
                           capture_output=True, text=True)
        # Docker Desktop iniciando junto com o Windows
        for nome in ("settings-store.json", "settings.json"):
            cfg = Path(os.environ.get("APPDATA", "")) / "Docker" / nome
            if cfg.exists():
                try:
                    dados = json.loads(cfg.read_text(encoding="utf-8"))
                    dados["AutoStart" if nome == "settings-store.json" else "autoStart"] = True
                    cfg.write_text(json.dumps(dados, indent=2), encoding="utf-8")
                except (OSError, json.JSONDecodeError):
                    pass
        return "tarefa agendada 'Assistente de voz' ao entrar no Windows" if r.returncode == 0 else f"falhou: {r.stderr.strip()}"
    if not shutil.which("systemctl"):
        return "sem systemd: os contêineres voltam sozinhos quando o Docker iniciar (restart: unless-stopped)"
    unidade = (RAIZ / "sistema" / "assistente.service").read_text(encoding="utf-8")
    unidade = unidade.replace("{RAIZ}", str(RAIZ)).replace("{PYTHON}", sys.executable).replace("{DOCKER}", shutil.which("docker") or "/usr/bin/docker")
    prefixo = [] if eh_root() else ["sudo"]
    destino = Path("/etc/systemd/system/assistente.service")
    subprocess.run([*prefixo, "tee", str(destino)], input=unidade, text=True, capture_output=True, check=True)
    subprocess.run([*prefixo, "systemctl", "daemon-reload"], check=True)
    subprocess.run([*prefixo, "systemctl", "enable", "docker"], check=False, capture_output=True)
    subprocess.run([*prefixo, "systemctl", "enable", "assistente.service"], check=True, capture_output=True)
    return "serviço systemd 'assistente' habilitado (sobe junto com o Docker no boot)"


def corrigir_dono() -> None:
    """Se rodou com sudo, devolve os arquivos gerados ao usuário."""
    usuario = os.environ.get("SUDO_USER")
    if eh_root() and usuario and usuario != "root":
        for nome in (".env", "estado", "logs", "relatorio-hardware.md", "relatorio-teste.md", "relatorio-instalacao.md"):
            caminho = RAIZ / nome
            if caminho.exists():
                subprocess.run(["chown", "-R", f"{usuario}:", str(caminho)], check=False)


def relatorio_final(env: dict, teste) -> str:
    ip = env["HOST_IP"]
    l = [f"# Instalação do assistente de voz — {dt.datetime.now():%d/%m/%Y %H:%M}", "",
         "## Endereços", "",
         f"- **Open WebUI (interface principal):** http://localhost:{env['PORTA_WEBUI']} — na rede: http://{ip}:{env['PORTA_WEBUI']} ou https://{ip}:{env['PORTA_HTTPS_WEBUI']}",
         f"- **Modo de voz avançado:** http://localhost:{env['PORTA_VOZ']} — na rede (com microfone): https://{ip}:{env['PORTA_HTTPS_VOZ']}",
         f"- **ComfyUI (opcional):** http://localhost:{env['PORTA_COMFYUI']}" if env.get("IMAGENS_ATIVAS") == "true" else "- **ComfyUI:** desativado",
         f"- **Login:** {env['ADMIN_EMAIL']} / senha no arquivo `.env` (ADMIN_SENHA)",
         "", "## O que ficou instalado", ""]
    llm = resultados.get("llm", {})
    fala = resultados.get("fala", {})
    stt, tts = fala.get("stt", {}), fala.get("tts", {})
    l += [
        f"- **LLM ativo:** `{env['LLM_MODELO_ATIVO']}` via {env['LLM_BACKEND']} (principal planejado: `{env['LLM_PRINCIPAL']}`, reserva: `{env['LLM_RESERVA']}`)",
        f"- **Reconhecimento de voz:** faster-whisper `{stt.get('modelo')}` em {stt.get('dispositivo')} ({stt.get('compute_type')}), português",
        f"- **Síntese de voz:** {tts.get('motor')} em {tts.get('dispositivo')} (voz `{env['TTS_VOZ']}`); reserva Piper pt_BR-faber-medium",
        "- **Imagens:** " + (f"ComfyUI + {'SDXL 1.0' if env['SD_MODELO'] == 'sdxl' else 'Stable Diffusion 1.5'}" if env.get("IMAGENS_ATIVAS") == "true" else "desativado"),
        "- **Busca na web:** SearXNG local (sem chave de API)",
        "- **Documentos:** PDF, DOCX, TXT e Markdown (servidor de ferramentas)",
        f"- **Início automático:** {resultados.get('autostart', '?')}",
    ]
    if llm:
        for chave in ("teste_principal_ollama", "teste_principal_llamacpp", "teste_reserva"):
            if chave in llm:
                t = llm[chave]
                l.append(f"  - {chave.replace('_', ' ')}: {'OK' if t['ok'] else 'falhou'} em {t['segundos']} s → “{t['resposta']}”")
    if avisos:
        l += ["", "## Avisos", ""] + [f"- ⚠️ {a}" for a in dict.fromkeys(avisos)]
    if teste is not None:
        l += ["", teste.relatorio().replace("# Teste", "## Teste", 1)]
    return "\n".join(l) + "\n"


# --------------------------------------------------------------------------- principal
def main() -> int:
    ap = argparse.ArgumentParser(description="Instala o assistente de voz local.")
    ap.add_argument("--forcar-omni", action="store_true", help="usa o Qwen3-Omni mesmo sem VRAM suficiente (parte na RAM)")
    ap.add_argument("--forcar-cpu", action="store_true", help="não usa GPU")
    ap.add_argument("--sem-imagens", action="store_true", help="não instala o Stable Diffusion")
    ap.add_argument("--pular-teste", action="store_true", help="não roda o teste de ponta a ponta")
    ap.add_argument("--sem-autostart", action="store_true", help="não configura o início automático")
    args = ap.parse_args()
    arq_log = iniciar_log("instalacao")
    log(f"Assistente de voz local — instalação ({platform.system()} {platform.release()}). Log: {arq_log.relative_to(RAIZ)}")
    teste = None
    try:
        log("Verificando o Docker", "passo")
        verificar_docker()

        log("Levantando o hardware e escolhendo os modelos", "passo")
        hw = detectar(args)
        env = ler_env()
        if env["MODO"] == "gpu" and not gpu_no_docker():
            avisos.append("O Docker não conseguiu usar a GPU (driver ou NVIDIA Container Toolkit). Instalado em modo CPU; "
                          "rode o instalador de novo depois de reiniciar o computador.")
            args.forcar_cpu = True
            hw = detectar(args)
            env = ler_env()
        if hw.get("ollama_nativo"):
            desativar_ollama_nativo()

        construir(env)

        log("Preparando os modelos de linguagem", "passo")
        resultados["llm"] = preparar_llm.preparar(env, avisos)
        env = ler_env()

        log("Preparando reconhecimento e síntese de voz", "passo")
        preparar_fala()

        log("Subindo todos os serviços", "passo")
        env = subir_tudo(env)

        log("Configurando o Open WebUI", "passo")
        cfg = configurar_openwebui.configurar(env)
        env = ler_env()
        for area, mudancas in cfg["ajustes"].items():
            for m in mudancas:
                log(f"{area}: {m}")
        if env["LLM_MODELO_ATIVO"] not in cfg["modelos"]:
            avisos.append(f"O modelo {env['LLM_MODELO_ATIVO']} não apareceu na lista do Open WebUI: {cfg['modelos']}")

        if not args.sem_autostart:
            log("Ligando o início automático", "passo")
            try:
                resultados["autostart"] = instalar_autostart(env)
                log(resultados["autostart"], "ok")
            except Exception as e:  # noqa: BLE001
                resultados["autostart"] = f"falhou: {e}"
                avisos.append(f"Não consegui configurar o início automático: {e}")
        else:
            resultados["autostart"] = "não configurado (--sem-autostart)"

        if not args.pular_teste:
            log("Teste de ponta a ponta", "passo")
            import teste_ponta_a_ponta

            teste = teste_ponta_a_ponta.executar(env)
            for r in teste.resultados:
                if not r["ok"]:
                    avisos.append(f"Teste “{r['etapa']}” falhou: {r['detalhe']}")
    except Exception as e:  # noqa: BLE001
        log(f"A instalação parou: {e}", "erro")
        avisos.append(f"Instalação interrompida: {e}")
        env = ler_env()
        (RAIZ / "relatorio-instalacao.md").write_text(relatorio_final(env, teste) if env else str(e), encoding="utf-8")
        corrigir_dono()
        return 1

    texto = relatorio_final(env, teste)
    (RAIZ / "relatorio-instalacao.md").write_text(texto, encoding="utf-8")
    corrigir_dono()
    print("\n" + "=" * 72 + "\n" + texto)
    log(f"Pronto! Abra http://localhost:{env['PORTA_WEBUI']} (texto e chamada de voz) ou http://localhost:{env['PORTA_VOZ']} (modo de voz avançado).", "ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
