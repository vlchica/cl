#!/usr/bin/env python3
"""Atualiza HOST_IP no .env quando o IP da rede local muda (roda a cada boot)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from comum import atualizar_env, ler_env  # noqa: E402
from detectar_hardware import ip_local  # noqa: E402

atual = ler_env().get("HOST_IP")
novo = ip_local()
if novo and novo != "127.0.0.1" and novo != atual:
    atualizar_env(HOST_IP=novo)
    print(f"HOST_IP atualizado: {atual} -> {novo}")
