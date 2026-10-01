"""Funções compartilhadas pelos scripts de instalação e teste (só biblioteca padrão)."""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

RAIZ = Path(__file__).resolve().parent.parent
ARQ_ENV = RAIZ / ".env"
PASTA_LOGS = RAIZ / "logs"
_ARQ_LOG: Path | None = None

# Conexões locais nunca passam por proxy do sistema
_abridor = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def iniciar_log(nome: str) -> Path:
    global _ARQ_LOG
    PASTA_LOGS.mkdir(exist_ok=True)
    _ARQ_LOG = PASTA_LOGS / f"{nome}-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
    return _ARQ_LOG


def log(msg: str, tipo: str = "info") -> None:
    marcas = {"info": "•", "ok": "✅", "aviso": "⚠️ ", "erro": "❌", "passo": "\n▶"}
    linha = f"{marcas.get(tipo, '•')} {msg}"
    print(linha, flush=True)
    if _ARQ_LOG:
        with _ARQ_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%H:%M:%S}] {linha}\n")


# --------------------------------------------------------------------------- .env
def ler_env() -> dict[str, str]:
    dados: dict[str, str] = {}
    if ARQ_ENV.exists():
        for linha in ARQ_ENV.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                dados[k.strip()] = v.strip()
    return dados


def atualizar_env(**valores: str) -> dict[str, str]:
    """Altera chaves do .env preservando a ordem e os comentários."""
    linhas = ARQ_ENV.read_text(encoding="utf-8").splitlines() if ARQ_ENV.exists() else []
    pendentes = dict(valores)
    for i, linha in enumerate(linhas):
        if "=" in linha and not linha.lstrip().startswith("#"):
            chave = linha.split("=", 1)[0].strip()
            if chave in pendentes:
                linhas[i] = f"{chave}={pendentes.pop(chave)}"
    linhas += [f"{k}={v}" for k, v in pendentes.items()]
    ARQ_ENV.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return ler_env()


# --------------------------------------------------------------------------- comandos
def rodar(cmd: list[str], verificar: bool = True, capturar: bool = False, timeout: float | None = None,
          entrada: str | None = None) -> subprocess.CompletedProcess:
    if _ARQ_LOG:
        with _ARQ_LOG.open("a", encoding="utf-8") as f:
            f.write(f"$ {' '.join(cmd)}\n")
    r = subprocess.run(cmd, cwd=RAIZ, text=True, capture_output=capturar, timeout=timeout, input=entrada,
                       encoding="utf-8", errors="replace")
    if verificar and r.returncode != 0:
        detalhe = (r.stderr or r.stdout or "").strip()[-2000:] if capturar else ""
        raise RuntimeError(f"comando falhou ({r.returncode}): {' '.join(cmd)}\n{detalhe}")
    return r


def docker_cmd() -> list[str]:
    return ["docker"]


def compose(*args: str, verificar: bool = True, capturar: bool = False, timeout: float | None = None) -> subprocess.CompletedProcess:
    return rodar(["docker", "compose", *args], verificar=verificar, capturar=capturar, timeout=timeout)


def exec_servico(servico: str, *cmd: str, timeout: float = 60) -> str:
    r = compose("exec", "-T", servico, *cmd, verificar=False, capturar=True, timeout=timeout)
    return r.stdout if r.returncode == 0 else ""


def tem(programa: str) -> bool:
    return shutil.which(programa) is not None


# --------------------------------------------------------------------------- HTTP
def http(metodo: str, url: str, corpo: Any = None, cabecalhos: dict | None = None, timeout: float = 30,
         bruto: bool = False) -> tuple[int, Any]:
    dados = None
    cab = dict(cabecalhos or {})
    if corpo is not None:
        if isinstance(corpo, (bytes, bytearray)):
            dados = bytes(corpo)
        else:
            dados = json.dumps(corpo).encode()
            cab.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=dados, headers=cab, method=metodo)
    try:
        with _abridor.open(req, timeout=timeout) as r:
            conteudo = r.read()
            status = r.status
            tipo = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        conteudo, status, tipo = e.read(), e.code, e.headers.get("Content-Type", "")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return 0, str(e)
    if bruto:
        return status, conteudo
    if "json" in tipo:
        try:
            return status, json.loads(conteudo)
        except json.JSONDecodeError:
            pass
    return status, conteudo.decode("utf-8", "replace")


def multipart(campos: dict[str, str], arquivos: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    limite = f"----assistente{int(time.time() * 1000)}"
    partes = []
    for nome, valor in campos.items():
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n{valor}\r\n'.encode())
    for nome, (arquivo, dados, tipo) in arquivos.items():
        partes.append(
            f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"; filename="{arquivo}"\r\n'
            f"Content-Type: {tipo}\r\n\r\n".encode() + dados + b"\r\n"
        )
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


def esperar(condicao: Callable[[], bool], limite_s: float, descricao: str, intervalo: float = 3) -> bool:
    inicio = time.time()
    ultimo_aviso = inicio
    while time.time() - inicio < limite_s:
        try:
            if condicao():
                return True
        except Exception:  # noqa: BLE001
            pass
        if time.time() - ultimo_aviso > 60:
            log(f"ainda aguardando {descricao} ({int(time.time() - inicio)} s)…")
            ultimo_aviso = time.time()
        time.sleep(intervalo)
    return False


def eh_windows() -> bool:
    return sys.platform.startswith("win")


def eh_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0
