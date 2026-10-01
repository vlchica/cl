#!/usr/bin/env bash
# Atalhos do dia a dia: ./assistente.sh iniciar|parar|status|logs [serviço]|testar|vram|atualizar|desinstalar
set -euo pipefail
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
case "${1:-status}" in
  iniciar)   $PY scripts/atualizar_ip.py || true; docker compose up -d --remove-orphans ;;
  parar)     docker compose stop ;;
  status)    docker compose ps; echo; $PY scripts/medir_vram.py ;;
  logs)      docker compose logs -f --tail 200 ${2:-} ;;
  testar)    $PY scripts/teste_ponta_a_ponta.py ;;
  vram)      $PY scripts/medir_vram.py ;;
  atualizar) git pull --ff-only && $PY scripts/instalar.py --pular-teste ;;
  reinstalar) $PY scripts/instalar.py "${@:2}" ;;
  desinstalar)
    read -r -p "Remover contêineres E modelos baixados (dezenas de GB)? [s/N] " r
    if [[ "$r" =~ ^[sS]$ ]]; then
      docker compose --profile imagens --profile llamacpp down -v --remove-orphans
      sudo systemctl disable --now assistente.service 2>/dev/null || true
      sudo rm -f /etc/systemd/system/assistente.service
    fi ;;
  *) echo "uso: $0 iniciar|parar|status|logs [serviço]|testar|vram|atualizar|reinstalar|desinstalar"; exit 1 ;;
esac
