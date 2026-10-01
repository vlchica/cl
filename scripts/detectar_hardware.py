#!/usr/bin/env python3
"""Levanta o hardware da máquina e escolhe os modelos que cabem nele.

Só usa a biblioteca padrão do Python, para rodar antes de qualquer
dependência estar instalada (Linux, WSL ou Windows).

Uso:
    python3 scripts/detectar_hardware.py            # detecta, planeja e grava .env
    python3 scripts/detectar_hardware.py --so-mostrar
    python3 scripts/detectar_hardware.py --simular '{"gpus":[{"nome":"RTX 3090","vram_mb":24576}],"ram_gb":64}'
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import secrets
import shutil
import socket
import string
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQ_ENV = RAIZ / ".env"
ARQ_PLANO = RAIZ / "estado" / "plano.json"
ARQ_RELATORIO = RAIZ / "relatorio-hardware.md"

# --------------------------------------------------------------------------
# Catálogo de modelos (tamanhos medidos nos arquivos publicados; VRAM inclui
# cache de contexto quantizado em q8_0 e uma folga de trabalho).
# --------------------------------------------------------------------------
QWEN3_OMNI = "hf.co/ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF:Q4_K_M"
QWEN3_OMNI_REPO = "ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF:Q4_K_M"

LLMS = [
    # chave, id no Ollama, disco GB, pesos na VRAM GB, cache KV q8_0 em GB por 1k tokens, descrição
    ("qwen3-omni", QWEN3_OMNI, 18.6, 18.6, 0.049, "Qwen3-Omni 30B-A3B Instruct, Q4_K_M (4 bits)"),
    ("qwen3-14b", "qwen3:14b", 9.3, 9.0, 0.082, "Qwen3 14B, Q4_K_M"),
    ("qwen3-8b", "qwen3:8b", 5.2, 4.9, 0.074, "Qwen3 8B, Q4_K_M"),
    ("qwen3-4b", "qwen3:4b", 2.6, 2.5, 0.074, "Qwen3 4B, Q4_K_M"),
    ("qwen3-1.7b", "qwen3:1.7b", 1.4, 1.4, 0.057, "Qwen3 1.7B, Q4_K_M"),
    ("qwen3-0.6b", "qwen3:0.6b", 0.5, 0.5, 0.057, "Qwen3 0.6B, Q4_K_M"),
]
BUFFERS_LLM_GB = 0.5  # buffers de computação do llama.cpp
MARGEM_LLM_GB = 0.3  # folga para não encostar no limite da placa


def vram_llm(chave: str, contexto: int) -> float:
    m = LLM_POR_CHAVE[chave]
    return round(m[3] + m[4] * contexto / 1024 + BUFFERS_LLM_GB, 1)


LLM_POR_CHAVE = {m[0]: m for m in LLMS}
DESC = 5  # índice da descrição

WHISPER_GPU = ("large-v3-turbo", 1.6, 1.3)  # modelo, disco GB, VRAM GB (int8_float16)
WHISPER_CPU = ("small", 0.5, 0.0)
KOKORO_VRAM = 0.6
SD = {
    "sdxl": {"disco": 6.9, "vram": 7.5, "tamanho": "1024x1024", "passos": 25},
    "sd15": {"disco": 2.1, "vram": 3.0, "tamanho": "512x512", "passos": 25},
}
# Imagens Docker (descompactadas, GB). A base PyTorch é compartilhada por fala e comfyui.
DISCO_IMAGENS_GPU = 4.5 + 9.3 + 10.5 + 1.5 + 0.6 + 0.4 + 0.3 + 0.1
DISCO_IMAGENS_CPU = 4.5 + 9.3 + 3.0 + 1.5 + 0.6 + 0.4 + 0.3 + 0.1
DISCO_LLAMACPP = 3.5
DISCO_OUTROS_MODELOS = 0.4 + 0.1 + 0.5  # kokoro + piper + embeddings
FOLGA_DISCO = 10.0
RESERVA_VRAM_POR_GPU = 0.5  # driver, contexto CUDA, tela

# Índice de rodas do PyTorch usado na imagem base (servicos/base/Dockerfile)
TORCH_CU126 = "cu126"  # GPUs de Maxwell (GTX 900) até Ada (RTX 40)
TORCH_CU128 = "cu128"  # necessário para Blackwell (RTX 50)
TORCH_CPU = "cpu"

PORTAS_PADRAO = {
    "PORTA_WEBUI": 3000,
    "PORTA_VOZ": 3001,
    "PORTA_ARQUIVOS": 3002,
    "PORTA_HTTPS_WEBUI": 3443,
    "PORTA_HTTPS_VOZ": 3444,
    "PORTA_OLLAMA": 11434,
    "PORTA_COMFYUI": 8188,
}


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def rodar(cmd: list[str], timeout: int = 20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def eh_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def gb(valor_bytes: float) -> float:
    return round(valor_bytes / 1024**3, 1)


# --------------------------------------------------------------------------
# Detecção
# --------------------------------------------------------------------------
def detectar_so() -> dict:
    so = {"sistema": platform.system(), "versao": platform.release(), "arquitetura": platform.machine(), "wsl": eh_wsl()}
    if so["sistema"] == "Linux":
        try:
            dados = dict(
                linha.split("=", 1) for linha in Path("/etc/os-release").read_text().splitlines() if "=" in linha
            )
            so["distribuicao"] = dados.get("PRETTY_NAME", "").strip('"')
            so["id"] = dados.get("ID", "").strip('"')
        except OSError:
            pass
    elif so["sistema"] == "Windows":
        so["distribuicao"] = f"Windows {platform.release()} ({platform.version()})"
    elif so["sistema"] == "Darwin":
        so["distribuicao"] = f"macOS {platform.mac_ver()[0]}"
    return so


def detectar_cpu() -> dict:
    modelo = platform.processor() or ""
    if Path("/proc/cpuinfo").exists():
        m = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text())
        if m:
            modelo = m.group(1).strip()
    elif platform.system() == "Windows":
        saida = rodar(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"])
        modelo = saida.strip() or modelo
    return {"modelo": modelo, "nucleos": os.cpu_count() or 1}


def detectar_ram_gb() -> float:
    if Path("/proc/meminfo").exists():
        m = re.search(r"MemTotal:\s+(\d+) kB", Path("/proc/meminfo").read_text())
        if m:
            return round(int(m.group(1)) / 1024**2, 1)
    if platform.system() == "Windows":
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        estado = MEMORYSTATUSEX()
        estado.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado))  # type: ignore[attr-defined]
        return gb(estado.ullTotalPhys)
    if platform.system() == "Darwin":
        saida = rodar(["sysctl", "-n", "hw.memsize"])
        if saida.strip().isdigit():
            return gb(int(saida.strip()))
    return 0.0


def raiz_docker() -> str | None:
    saida = rodar(["docker", "info", "--format", "{{.DockerRootDir}}"]).strip()
    return saida or None


def detectar_discos() -> list[dict]:
    caminhos = {"projeto": str(RAIZ)}
    rd = raiz_docker()
    if rd and Path(rd).exists():
        caminhos["docker"] = rd
    elif platform.system() == "Windows":
        # Docker Desktop guarda as imagens no disco virtual do WSL, em geral no C:
        caminhos["docker"] = os.environ.get("LOCALAPPDATA", "C:\\")
    discos = []
    vistos = set()
    for nome, caminho in caminhos.items():
        try:
            uso = shutil.disk_usage(caminho)
        except OSError:
            continue
        chave = (uso.total, uso.free)
        discos.append(
            {
                "nome": nome,
                "caminho": caminho,
                "total_gb": gb(uso.total),
                "livre_gb": gb(uso.free),
                "mesmo_disco_que_projeto": chave in vistos,
            }
        )
        vistos.add(chave)
    return discos


def detectar_gpus_nvidia() -> tuple[list[dict], str | None]:
    exe = shutil.which("nvidia-smi") or (
        "C:\\Windows\\System32\\nvidia-smi.exe" if Path("C:\\Windows\\System32\\nvidia-smi.exe").exists() else None
    )
    if not exe:
        return [], None
    campos = "index,name,memory.total,memory.used,memory.free,compute_cap,driver_version,uuid,pci.bus_id"
    saida = rodar([exe, f"--query-gpu={campos}", "--format=csv,noheader,nounits"])
    gpus = []
    driver = None
    for linha in saida.strip().splitlines():
        partes = [p.strip() for p in linha.split(",")]
        if len(partes) < 9:
            continue
        idx, nome, total, usada, livre, cc, drv, uuid, bus = partes[:9]
        driver = drv
        try:
            cc_num = float(cc)
        except ValueError:
            cc_num = 0.0
        gpus.append(
            {
                "indice": int(idx),
                "fabricante": "nvidia",
                "nome": nome,
                "vram_mb": int(float(total)),
                "vram_usada_mb": int(float(usada)) if usada.replace(".", "").isdigit() else 0,
                "vram_livre_mb": int(float(livre)) if livre.replace(".", "").isdigit() else int(float(total)),
                "compute_cap": cc_num,
                "uuid": uuid,
                "pci": bus,
            }
        )
    # Processos que já ocupam VRAM (mineradores, jogos, outro Ollama...)
    proc = rodar([exe, "--query-compute-apps=gpu_uuid,pid,process_name,used_memory", "--format=csv,noheader,nounits"])
    for linha in proc.strip().splitlines():
        partes = [p.strip() for p in linha.split(",")]
        if len(partes) < 4:
            continue
        for g in gpus:
            if g["uuid"] == partes[0]:
                g.setdefault("processos", []).append(
                    {"pid": partes[1], "nome": Path(partes[2]).name, "vram_mb": partes[3]}
                )
    return gpus, driver


def detectar_outras_gpus() -> list[dict]:
    """GPUs AMD/Intel só são listadas para o relatório (o plano usa apenas NVIDIA/CUDA)."""
    outras = []
    saida = rodar(["lspci"]) if shutil.which("lspci") else ""
    if not saida and platform.system() == "Windows":
        saida = rodar(
            ["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"]
        )
    for linha in saida.splitlines():
        baixa = linha.lower()
        if ("vga" in baixa or "3d" in baixa or "display" in baixa or platform.system() == "Windows") and (
            "amd" in baixa or "radeon" in baixa or "intel" in baixa or "ati " in baixa
        ):
            outras.append({"descricao": linha.strip()})
    return outras


def detectar_ollama_nativo(porta: int = 11434) -> bool:
    """Um Ollama instalado fora do Docker disputaria a porta e a VRAM."""
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=1) as s:
            s.sendall(b"GET /api/version HTTP/1.0\r\nHost: localhost\r\n\r\n")
            resposta = s.recv(512).decode(errors="ignore")
        return '"version"' in resposta
    except OSError:
        return False


def ip_local() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def porta_livre(porta: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", porta))
            return True
        except OSError:
            return False


def portas_dos_nossos_containers() -> set[int]:
    """Portas já publicadas pelo próprio assistente (numa reinstalação) não contam como conflito."""
    saida = rodar(["docker", "ps", "--filter", "label=com.docker.compose.project=assistente", "--format", "{{.Ports}}"])
    return {int(p) for p in re.findall(r":(\d+)->", saida)}


def escolher_portas(atuais: dict[str, str]) -> tuple[dict[str, int], list[str]]:
    nossas = portas_dos_nossos_containers()
    usadas: set[int] = set()
    escolhidas: dict[str, int] = {}
    avisos = []
    for chave, padrao in PORTAS_PADRAO.items():
        desejada = int(atuais.get(chave, padrao))
        porta = desejada
        while porta in usadas or (porta not in nossas and not porta_livre(porta)):
            porta += 1
        if porta != desejada:
            avisos.append(f"Porta {desejada} ocupada por outro programa; {chave} usará {porta}.")
        usadas.add(porta)
        escolhidas[chave] = porta
    return escolhidas, avisos


def detectar() -> dict:
    gpus, driver = detectar_gpus_nvidia()
    return {
        "data": dt.datetime.now().isoformat(timespec="seconds"),
        "so": detectar_so(),
        "cpu": detectar_cpu(),
        "ram_gb": detectar_ram_gb(),
        "discos": detectar_discos(),
        "gpus": gpus,
        "driver_nvidia": driver,
        "outras_gpus": detectar_outras_gpus(),
        "ollama_nativo": detectar_ollama_nativo(),
        "ip_local": ip_local(),
    }


# --------------------------------------------------------------------------
# Planejamento
# --------------------------------------------------------------------------
def versao_driver(driver: str | None) -> float:
    try:
        return float(".".join((driver or "0").split(".")[:2]))
    except ValueError:
        return 0.0


def vram_util_gb(g: dict) -> float:
    return max(0.0, g["vram_mb"] / 1024 - RESERVA_VRAM_POR_GPU)


def escolher_llm(orcamento_gb: float, forcar_omni: bool, ram_gb: float) -> tuple[str, int, list[str]]:
    """Maior modelo que cabe no orçamento de VRAM, preferindo contexto de 16k e aceitando 8k."""
    avisos = []
    if forcar_omni:
        if orcamento_gb < vram_llm("qwen3-omni", 8192):
            avisos.append(
                f"Qwen3-Omni forçado com {orcamento_gb:.1f} GB de VRAM para o LLM: "
                "parte do modelo ficará na RAM (mais lento)."
            )
        return "qwen3-omni", 16384 if orcamento_gb >= vram_llm("qwen3-omni", 16384) else 8192, avisos
    for chave, *_ in LLMS:
        for contexto in (16384, 8192):
            if vram_llm(chave, contexto) + MARGEM_LLM_GB <= orcamento_gb:
                return chave, contexto, avisos
    # Sem VRAM suficiente: roda na CPU, escolhido pela RAM
    if ram_gb >= 24:
        return "qwen3-8b", 8192, avisos
    if ram_gb >= 12:
        return "qwen3-4b", 8192, avisos
    if ram_gb >= 6:
        return "qwen3-1.7b", 8192, avisos
    return "qwen3-0.6b", 8192, avisos


def reserva_de(chave: str) -> str:
    ordem = [m[0] for m in LLMS]
    i = ordem.index(chave)
    return ordem[min(i + 1, len(ordem) - 1)]


def planejar(hw: dict, forcar_omni: bool = False, sem_imagens: bool = False, forcar_cpu: bool = False) -> dict:
    avisos: list[str] = []
    notas: list[str] = []
    gpus = [] if forcar_cpu else sorted(hw.get("gpus", []), key=lambda g: -g["vram_mb"])
    ram = hw.get("ram_gb", 0) or 0
    gpu_ok = [g for g in gpus if g.get("compute_cap", 5.0) >= 5.0 and g["vram_mb"] >= 3500]
    for g in gpus:
        if g not in gpu_ok:
            avisos.append(f"GPU {g['indice']} ({g['nome']}) é antiga ou pequena demais e será ignorada.")
        ocupada = g.get("vram_usada_mb", 0)
        if ocupada > 1024:
            nomes = ", ".join(p["nome"] for p in g.get("processos", [])) or "outros programas"
            avisos.append(
                f"GPU {g['indice']} ({g['nome']}) já tem {ocupada / 1024:.1f} GB ocupados por {nomes}. "
                "Se for mineração ou jogo, pause enquanto usa o assistente; senão o modelo "
                "será dividido com a RAM e ficará mais lento."
            )

    plano: dict = {"modo": "gpu" if gpu_ok else "cpu"}

    if hw.get("outras_gpus") and not gpu_ok:
        avisos.append(
            "Só encontrei GPU AMD/Intel. Esta instalação acelera apenas GPUs NVIDIA (CUDA); "
            "tudo vai rodar na CPU."
        )

    drv = versao_driver(hw.get("driver_nvidia"))
    if gpu_ok and drv and drv < 535:
        avisos.append(
            f"Driver NVIDIA {hw.get('driver_nvidia')} é antigo para CUDA 12. O instalador vai tentar "
            "atualizar; se não conseguir, atualize para a versão 550 ou mais nova."
        )

    # ---------------- Distribuição das GPUs ----------------
    if gpu_ok:
        cc_max = max(g["compute_cap"] for g in gpu_ok)
        cc_min = min(g["compute_cap"] for g in gpu_ok)
        plano["torch_indice"] = TORCH_CU128 if cc_max >= 10.0 else TORCH_CU126
        if cc_max >= 10.0 and cc_min < 7.5:
            avisos.append("Mistura de GPU Blackwell (RTX 50) com GPU antiga: as antigas ficam fora do PyTorch.")
        if cc_max >= 10.0 and drv and drv < 570:
            avisos.append("GPUs RTX 50 precisam do driver 570 ou mais novo.")

        aux = None
        llm_gpus = list(gpu_ok)
        if len(gpu_ok) >= 2:
            # Uma GPU menor fica para voz + imagens; as maiores para o LLM.
            for limiar in (9.5, 5.0):
                candidatas = [g for g in reversed(gpu_ok) if vram_util_gb(g) >= limiar]
                if candidatas:
                    aux = candidatas[0]
                    break
            if aux is not None:
                restantes = [g for g in gpu_ok if g is not aux]
                if sum(vram_util_gb(g) for g in restantes) >= 6.6:
                    llm_gpus = restantes
                else:
                    aux = None
        orc_llm = sum(vram_util_gb(g) for g in llm_gpus)
        compartilhada = aux is None
        if compartilhada:
            aux = llm_gpus[0]
            orc_llm -= WHISPER_GPU[2]  # o Whisper fica sempre carregado na mesma GPU
        plano["gpus_llm"] = ",".join(str(g["indice"]) for g in llm_gpus)
        plano["gpus_aux"] = str(aux["indice"])
        plano["gpu_compartilhada"] = compartilhada

        chave, contexto, av = escolher_llm(orc_llm, forcar_omni, ram)
        avisos += av
        if chave != "qwen3-omni" and not forcar_omni:
            avisos.append(
                f"O Qwen3-Omni 4 bits precisa de ~{vram_llm('qwen3-omni', 8192):.0f} GB de VRAM e esta máquina "
                f"tem ~{orc_llm:.1f} GB disponíveis para o LLM: NÃO CABE. Modelo principal: {LLM_POR_CHAVE[chave][DESC]}. "
                "(Para forçar o Omni com parte na RAM: instalar com --forcar-omni.)"
            )
        sobra_llm_gpu = orc_llm - vram_llm(chave, contexto)

        # Whisper
        cc_aux = aux["compute_cap"]
        plano["whisper_modelo"] = WHISPER_GPU[0]
        plano["whisper_dispositivo"] = "cuda"
        plano["whisper_compute"] = "int8_float16" if cc_aux >= 7.0 else "int8"

        # Kokoro na GPU só se sobrar memória; na CPU ele já fala mais rápido que o tempo real.
        if compartilhada:
            sobra = min(sobra_llm_gpu, vram_util_gb(aux) - WHISPER_GPU[2])
        else:
            sobra = vram_util_gb(aux) - WHISPER_GPU[2]
        plano["tts_dispositivo"] = "cuda" if sobra >= KOKORO_VRAM + 2.0 else "cpu"
        if plano["tts_dispositivo"] == "cuda":
            sobra -= KOKORO_VRAM

        # Imagens
        if sem_imagens:
            plano["sd_modelo"] = "nenhum"
        else:
            vram_aux_total = vram_util_gb(aux)
            plano["sd_modelo"] = "sdxl" if vram_aux_total >= 9.5 else "sd15"
            precisa = SD[plano["sd_modelo"]]["vram"]
            plano["liberar_vram_para_imagem"] = sobra < precisa
            if plano["liberar_vram_para_imagem"]:
                notas.append(
                    "Não há VRAM para LLM e Stable Diffusion carregados juntos: antes de cada imagem o LLM "
                    "é descarregado e volta sozinho em seguida (alguns segundos a mais por imagem)."
                )
            plano["comfyui_args"] = "--normalvram" if vram_aux_total >= precisa + 1 else "--lowvram"
        plano["llm_contexto"] = contexto
    else:
        plano["torch_indice"] = TORCH_CPU
        plano["gpus_llm"] = ""
        plano["gpus_aux"] = ""
        plano["gpu_compartilhada"] = False
        chave, contexto, av = escolher_llm(0, forcar_omni and ram >= 40, ram)
        avisos += av
        avisos.append(
            "Nenhuma GPU NVIDIA utilizável: tudo roda na CPU. Funciona, mas as respostas e as imagens "
            "serão lentas e o Qwen3-Omni não cabe."
        )
        plano["whisper_modelo"] = WHISPER_CPU[0]
        plano["whisper_dispositivo"] = "cpu"
        plano["whisper_compute"] = "int8"
        plano["tts_dispositivo"] = "cpu"
        plano["sd_modelo"] = "nenhum" if (sem_imagens or ram < 12) else "sd15"
        if plano["sd_modelo"] == "sd15":
            notas.append("Imagens na CPU: cada imagem 512x512 leva de 1 a 3 minutos.")
        elif not sem_imagens:
            avisos.append("RAM insuficiente para gerar imagens na CPU: geração de imagens desativada.")
        plano["liberar_vram_para_imagem"] = False
        plano["comfyui_args"] = "--cpu"
        plano["llm_contexto"] = contexto

    plano["llm_principal_chave"] = chave
    plano["llm_principal"] = LLM_POR_CHAVE[chave][1]
    plano["llm_reserva_chave"] = reserva_de(chave)
    plano["llm_reserva"] = LLM_POR_CHAVE[plano["llm_reserva_chave"]][1]

    # ---------------- Disco ----------------
    livre = min((d["livre_gb"] for d in hw.get("discos", [])), default=0.0)

    def necessario() -> float:
        total = DISCO_IMAGENS_GPU if plano["modo"] == "gpu" else DISCO_IMAGENS_CPU
        total += LLM_POR_CHAVE[plano["llm_principal_chave"]][2] + LLM_POR_CHAVE[plano["llm_reserva_chave"]][2]
        if plano["llm_principal_chave"] == "qwen3-omni":
            total += DISCO_LLAMACPP
        total += WHISPER_GPU[1] if plano["whisper_dispositivo"] == "cuda" else WHISPER_CPU[1]
        total += DISCO_OUTROS_MODELOS
        if plano.get("sd_modelo") in SD:
            total += SD[plano["sd_modelo"]]["disco"]
        return total + FOLGA_DISCO

    if livre and necessario() > livre:
        original = (plano["llm_principal"], plano["llm_reserva"], plano.get("sd_modelo"))
        ordem = [m[0] for m in LLMS]

        def tentativas():
            if plano.get("sd_modelo") == "sdxl":
                yield {"sd_modelo": "sd15"}
            for menor in ("qwen3-4b", "qwen3-1.7b"):
                if ordem.index(plano["llm_reserva_chave"]) < ordem.index(menor):
                    yield {"llm_reserva_chave": menor}
            while ordem.index(plano["llm_principal_chave"]) < ordem.index("qwen3-4b"):
                principal = reserva_de(plano["llm_principal_chave"])
                yield {"llm_principal_chave": principal, "llm_reserva_chave": "qwen3-1.7b"}

        for mudanca in tentativas():
            plano.update(mudanca)
            plano["llm_principal"] = LLM_POR_CHAVE[plano["llm_principal_chave"]][1]
            plano["llm_reserva"] = LLM_POR_CHAVE[plano["llm_reserva_chave"]][1]
            if necessario() <= livre:
                break
        mudou = []
        if plano["llm_principal"] != original[0]:
            mudou.append(f"modelo principal {original[0]} NÃO CABE no disco → {plano['llm_principal']}")
        if plano["llm_reserva"] != original[1]:
            mudou.append(f"reserva {original[1]} → {plano['llm_reserva']}")
        if plano.get("sd_modelo") != original[2]:
            mudou.append("Stable Diffusion SDXL → SD 1.5")
        if mudou:
            avisos.append(f"Pouco disco ({livre:.0f} GB livres): " + "; ".join(mudou) + ".")
        if necessario() > livre:
            avisos.append(
                f"Disco insuficiente: são necessários ~{necessario():.0f} GB e há {livre:.0f} GB livres. "
                "Libere espaço antes de continuar."
            )
    plano["disco_necessario_gb"] = round(necessario(), 1)
    plano["disco_livre_gb"] = livre

    # ---------------- RAM ----------------
    if plano["llm_principal_chave"] == "qwen3-omni" and ram and ram < 24:
        avisos.append(
            f"Só {ram:.0f} GB de RAM: carregar o Qwen3-Omni (18,6 GB) vai ser lento. Recomendo 32 GB ou mais."
        )
    if ram and ram < 8:
        avisos.append(f"Só {ram:.0f} GB de RAM: o conjunto todo pode não caber. Recomendo pelo menos 16 GB.")

    if plano.get("sd_modelo") in SD:
        plano["imagem_tamanho"] = SD[plano["sd_modelo"]]["tamanho"]
        plano["imagem_passos"] = SD[plano["sd_modelo"]]["passos"]
    else:
        plano["imagem_tamanho"] = "512x512"
        plano["imagem_passos"] = 20
        plano["liberar_vram_para_imagem"] = False
        plano.setdefault("comfyui_args", "--cpu")

    if hw.get("ollama_nativo"):
        avisos.append(
            "Já existe um Ollama rodando fora do Docker na porta 11434. Ele disputa VRAM com o assistente; "
            "o instalador vai desativá-lo (os modelos dele continuam no disco)."
        )

    plano["tts_voz"] = "pf_dora"
    plano["avisos"] = avisos
    plano["notas"] = notas
    return plano


# --------------------------------------------------------------------------
# Saída: .env, plano.json e relatório
# --------------------------------------------------------------------------
def ler_env(caminho: Path = ARQ_ENV) -> dict[str, str]:
    dados: dict[str, str] = {}
    if caminho.exists():
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                dados[k.strip()] = v.strip()
    return dados


def gravar_env(valores: dict[str, str], caminho: Path = ARQ_ENV) -> None:
    linhas = [f"# Gerado por scripts/detectar_hardware.py em {dt.datetime.now():%d/%m/%Y %H:%M}."]
    linhas.append("# Pode editar; rodar o instalador de novo preserva senhas e chaves.")
    for k, v in valores.items():
        linhas.append(f"{k}={v}")
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    try:
        os.chmod(caminho, 0o600)
    except OSError:
        pass


def segredo(n: int = 32) -> str:
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(n))


def montar_env(hw: dict, plano: dict, atuais: dict[str, str], portas: dict[str, int]) -> dict[str, str]:
    gpu = plano["modo"] == "gpu"
    arquivos_compose = ["docker-compose.yml"] + (["docker-compose.gpu.yml"] if gpu else [])
    imagens = plano["sd_modelo"] != "nenhum"
    # Decisões tomadas pelo instalador (Ollama x llama.cpp) valem enquanto o modelo principal for o mesmo
    mesmo_modelo = atuais.get("LLM_PRINCIPAL") == plano["llm_principal"]
    backend = atuais.get("LLM_BACKEND", "ollama") if mesmo_modelo else "ollama"
    ativo = atuais.get("LLM_MODELO_ATIVO", plano["llm_principal"]) if mesmo_modelo else plano["llm_principal"]
    perfis = (["imagens"] if imagens else []) + (["llamacpp"] if backend == "llamacpp" else [])
    return {
        "COMPOSE_PROJECT_NAME": "assistente",
        "COMPOSE_PATH_SEPARATOR": ":",
        "COMPOSE_FILE": ":".join(arquivos_compose),
        "COMPOSE_PROFILES": ",".join(perfis),
        "MODO": plano["modo"],
        "HOST_IP": hw.get("ip_local", "127.0.0.1"),
        **{k: str(v) for k, v in portas.items()},
        "GPUS_LLM": plano["gpus_llm"],
        "GPUS_AUX": plano["gpus_aux"],
        "TORCH_INDICE": plano["torch_indice"],
        "LLM_PRINCIPAL": plano["llm_principal"],
        "LLM_RESERVA": plano["llm_reserva"],
        "LLM_CONTEXTO": str(plano["llm_contexto"]),
        "LLM_BACKEND": backend,
        "LLM_MODELO_ATIVO": ativo,
        "USAR_LLAMACPP": "true" if backend == "llamacpp" else "false",
        "LLM_URL_VOZ": "http://llamacpp:8080/v1" if backend == "llamacpp" else "http://ollama:11434/v1",
        "LLAMACPP_REPO": QWEN3_OMNI_REPO,
        "LLAMACPP_ARQUIVO": atuais.get("LLAMACPP_ARQUIVO", "") if mesmo_modelo else "",
        # Folga de VRAM que o llama.cpp deixa livre (em MiB) para Whisper e Stable Diffusion
        "LLAMACPP_FOLGA_VRAM_MB": "4096" if plano.get("liberar_vram_para_imagem") else "1024",
        "WHISPER_MODELO": plano["whisper_modelo"],
        "WHISPER_DISPOSITIVO": plano["whisper_dispositivo"],
        "WHISPER_COMPUTE": plano["whisper_compute"],
        "TTS_VOZ": atuais.get("TTS_VOZ", plano["tts_voz"]),
        "TTS_DISPOSITIVO": plano["tts_dispositivo"],
        "SD_MODELO": plano["sd_modelo"],
        "IMAGENS_ATIVAS": "true" if imagens else "false",
        "IMAGEM_TAMANHO": plano["imagem_tamanho"],
        "IMAGEM_PASSOS": str(plano["imagem_passos"]),
        "COMFYUI_ARGS": plano["comfyui_args"],
        "LIBERAR_VRAM_PARA_IMAGEM": "1" if plano.get("liberar_vram_para_imagem") else "0",
        "FUSO_HORARIO": atuais.get("FUSO_HORARIO", "America/Sao_Paulo"),
        "WEBUI_SECRET_KEY": atuais.get("WEBUI_SECRET_KEY") or segredo(48),
        "FERRAMENTAS_TOKEN": atuais.get("FERRAMENTAS_TOKEN") or segredo(40),
        "SEARXNG_SECRET": atuais.get("SEARXNG_SECRET") or segredo(48),
        "ADMIN_EMAIL": atuais.get("ADMIN_EMAIL", "admin@assistente.local"),
        "ADMIN_SENHA": atuais.get("ADMIN_SENHA") or segredo(16),
        "OPENWEBUI_API_KEY": atuais.get("OPENWEBUI_API_KEY", ""),
    }


def relatorio_md(hw: dict, plano: dict) -> str:
    l = ["# Relatório de hardware", "", f"Gerado em {hw['data']}.", "", "## Máquina", ""]
    so = hw["so"]
    l.append(f"- **Sistema:** {so.get('distribuicao') or so['sistema']} ({so['arquitetura']})"
             + (" — rodando no WSL" if so.get("wsl") else ""))
    l.append(f"- **CPU:** {hw['cpu']['modelo']} — {hw['cpu']['nucleos']} threads")
    l.append(f"- **RAM:** {hw['ram_gb']} GB")
    for d in hw["discos"]:
        if not d.get("mesmo_disco_que_projeto"):
            l.append(f"- **Disco ({d['nome']}: {d['caminho']}):** {d['livre_gb']} GB livres de {d['total_gb']} GB")
    if hw["gpus"]:
        l.append(f"- **Driver NVIDIA:** {hw.get('driver_nvidia')}")
        for g in hw["gpus"]:
            l.append(
                f"- **GPU {g['indice']}:** {g['nome']} — {g['vram_mb'] / 1024:.1f} GB de VRAM "
                f"({g['vram_usada_mb'] / 1024:.1f} GB em uso), compute capability {g['compute_cap']}"
            )
    else:
        l.append("- **GPU NVIDIA:** nenhuma encontrada")
    for o in hw.get("outras_gpus", []):
        l.append(f"- **Outra GPU:** {o['descricao']}")
    l += ["", "## Escolhas", ""]
    llm = LLM_POR_CHAVE[plano["llm_principal_chave"]]
    res = LLM_POR_CHAVE[plano["llm_reserva_chave"]]
    l.append(f"- **Modo:** {'GPU' if plano['modo'] == 'gpu' else 'CPU'}"
             + (f" — LLM nas GPUs {plano['gpus_llm']}, voz/imagens na GPU {plano['gpus_aux']}" if plano["modo"] == "gpu" else ""))
    ctx = plano["llm_contexto"]
    mem = "VRAM" if plano["modo"] == "gpu" else "RAM"
    l.append(f"- **LLM principal:** {llm[DESC]} (`{llm[1]}`) — ~{vram_llm(llm[0], ctx):.1f} GB de {mem}")
    l.append(f"- **LLM de reserva:** {res[DESC]} (`{res[1]}`) — ~{vram_llm(res[0], ctx):.1f} GB de {mem}")
    l.append(f"- **Contexto:** {plano['llm_contexto']} tokens")
    l.append(f"- **Reconhecimento de voz:** faster-whisper `{plano['whisper_modelo']}` em {plano['whisper_dispositivo']} ({plano['whisper_compute']})")
    l.append(f"- **Síntese de voz:** Kokoro-82M pt-BR (voz `{plano['tts_voz']}`) em {plano['tts_dispositivo']}; reserva: Piper `pt_BR-faber-medium`")
    sd = plano.get("sd_modelo")
    l.append("- **Imagens:** " + ("desativado" if sd == "nenhum" else f"ComfyUI + {'SDXL 1.0' if sd == 'sdxl' else 'Stable Diffusion 1.5'} ({plano['imagem_tamanho']}, {plano['comfyui_args']})"))
    l.append(f"- **Disco necessário:** ~{plano['disco_necessario_gb']} GB (livre: {plano['disco_livre_gb']} GB)")
    if plano["avisos"]:
        l += ["", "## Avisos", ""] + [f"- ⚠️ {a}" for a in plano["avisos"]]
    if plano["notas"]:
        l += ["", "## Observações", ""] + [f"- {n}" for n in plano["notas"]]
    return "\n".join(l) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--so-mostrar", action="store_true", help="só mostra, não grava nada")
    ap.add_argument("--simular", help="JSON com hardware fictício (para testes)")
    ap.add_argument("--forcar-omni", action="store_true", help="usa o Qwen3-Omni mesmo sem VRAM suficiente")
    ap.add_argument("--forcar-cpu", action="store_true", help="ignora as GPUs")
    ap.add_argument("--sem-imagens", action="store_true", help="não instala o gerador de imagens")
    ap.add_argument("--json", action="store_true", help="imprime o plano em JSON")
    a = ap.parse_args()

    if a.simular:
        base = {"data": dt.datetime.now().isoformat(timespec="seconds"),
                "so": {"sistema": "Linux", "arquitetura": "x86_64", "distribuicao": "simulado"},
                "cpu": {"modelo": "simulado", "nucleos": 8}, "ram_gb": 32,
                "discos": [{"nome": "projeto", "caminho": "/", "total_gb": 1000, "livre_gb": 500}],
                "gpus": [], "driver_nvidia": "570.10", "outras_gpus": [], "ollama_nativo": False,
                "ip_local": "127.0.0.1"}
        sim = json.loads(a.simular)
        for i, g in enumerate(sim.pop("gpus", [])):
            g.setdefault("indice", i)
            g.setdefault("fabricante", "nvidia")
            g.setdefault("vram_usada_mb", 0)
            g.setdefault("compute_cap", 8.6)
            g.setdefault("uuid", f"GPU-{i}")
            base["gpus"].append(g)
        base.update(sim)
        hw = base
    else:
        hw = detectar()

    atuais = ler_env() if not a.simular else {}
    forcar_omni = a.forcar_omni or atuais.get("FORCAR_OMNI") == "1"
    plano = planejar(hw, forcar_omni=forcar_omni, sem_imagens=a.sem_imagens, forcar_cpu=a.forcar_cpu)

    if a.json:
        print(json.dumps({"hardware": hw, "plano": plano}, ensure_ascii=False, indent=2))
    else:
        print(relatorio_md(hw, plano))

    if a.so_mostrar or a.simular:
        return 0

    portas, avisos_portas = escolher_portas(atuais)
    plano["avisos"] += avisos_portas
    env = montar_env(hw, plano, atuais, portas)
    if forcar_omni:
        env["FORCAR_OMNI"] = "1"
    gravar_env(env)
    ARQ_PLANO.parent.mkdir(parents=True, exist_ok=True)
    ARQ_PLANO.write_text(json.dumps({"hardware": hw, "plano": plano}, ensure_ascii=False, indent=2), encoding="utf-8")
    ARQ_RELATORIO.write_text(relatorio_md(hw, plano), encoding="utf-8")
    for aviso in avisos_portas:
        print(f"⚠️ {aviso}")
    print(f"\nPlano gravado em {ARQ_ENV.name}, {ARQ_PLANO.relative_to(RAIZ)} e {ARQ_RELATORIO.name}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
