#!/usr/bin/env bash
# Instalador do assistente de voz local para Linux e WSL2.
# Instala o que faltar (driver NVIDIA, Docker, NVIDIA Container Toolkit, Python)
# e chama scripts/instalar.py, que faz o resto. Não pergunta nada.
#
#   ./instalar.sh                 instalação completa
#   ./instalar.sh --forcar-omni   usa o Qwen3-Omni mesmo sem VRAM suficiente
#   ./instalar.sh --forcar-cpu | --sem-imagens | --pular-teste | --sem-autostart
set -euo pipefail

RAIZ="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$RAIZ"

if [ "$(id -u)" -ne 0 ]; then
  echo "Pedindo permissão de administrador (sudo) para instalar driver, Docker e serviço de inicialização…"
  exec sudo -E bash "$0" "$@"
fi

passo() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
aviso() { printf '\033[1;33m⚠️  %s\033[0m\n' "$*"; }
mkdir -p logs
exec > >(tee -a "logs/instalar-sh-$(date +%Y%m%d-%H%M%S).log") 2>&1

# shellcheck disable=SC1091
. /etc/os-release
FAMILIA="${ID_LIKE:-$ID}"
WSL=0; grep -qi microsoft /proc/version 2>/dev/null && WSL=1
USUARIO="${SUDO_USER:-root}"

instalar_pacotes() {
  if command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@"
  elif command -v dnf >/dev/null; then
    dnf install -y -q "$@"
  elif command -v pacman >/dev/null; then
    pacman -Sy --noconfirm --needed "$@"
  elif command -v zypper >/dev/null; then
    zypper --non-interactive install "$@"
  else
    aviso "Gerenciador de pacotes não reconhecido; instale manualmente: $*"
  fi
}

continuar_apos_reiniciar() {
  # Agenda a continuação da instalação no próximo boot e reinicia com 2 minutos de aviso
  cat > /etc/systemd/system/assistente-continuar-instalacao.service <<UNIT
[Unit]
Description=Continua a instalação do assistente de voz após reiniciar
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
Environment=SUDO_USER=$USUARIO
ExecStart=/bin/bash -c '$RAIZ/instalar.sh $*; systemctl disable assistente-continuar-instalacao.service'
[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable assistente-continuar-instalacao.service
  aviso "O driver NVIDIA foi instalado e precisa de reinício. O computador reinicia em 2 minutos"
  aviso "e a instalação continua sozinha depois (log em $RAIZ/logs). Para adiar: sudo shutdown -c"
  shutdown -r +2 "Reinício para ativar o driver NVIDIA (assistente de voz)"
  exit 0
}

passo "Sistema: ${PRETTY_NAME:-$ID} $( [ $WSL = 1 ] && echo '(WSL2)')"
instalar_pacotes curl ca-certificates python3 pciutils gnupg

# ---------------------------------------------------------------- driver NVIDIA
TEM_NVIDIA=0
if [ $WSL = 1 ]; then
  # No WSL o driver é o do Windows; a GPU aparece em /usr/lib/wsl
  command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1 && TEM_NVIDIA=1
elif lspci 2>/dev/null | grep -qi 'nvidia'; then
  TEM_NVIDIA=1
fi

if [ $TEM_NVIDIA = 1 ] && [ $WSL = 0 ]; then
  passo "Driver NVIDIA"
  VERSAO=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | cut -d. -f1 || true)
  if [ -z "$VERSAO" ] || [ "$VERSAO" -lt 535 ]; then
    echo "Driver ausente ou antigo (${VERSAO:-nenhum}); instalando o recomendado…"
    if [[ "$FAMILIA" == *ubuntu* || "$ID" == ubuntu ]]; then
      instalar_pacotes ubuntu-drivers-common
      ubuntu-drivers install || ubuntu-drivers autoinstall
    elif [[ "$FAMILIA" == *debian* || "$ID" == debian ]]; then
      sed -i -E 's/^(deb .* main)( contrib)?( non-free)?( non-free-firmware)?$/\1 contrib non-free non-free-firmware/' /etc/apt/sources.list 2>/dev/null || true
      instalar_pacotes linux-headers-amd64 nvidia-driver firmware-misc-nonfree
    elif [[ "$FAMILIA" == *fedora* || "$ID" == fedora ]]; then
      dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" \
                     "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm" || true
      dnf install -y akmod-nvidia xorg-x11-drv-nvidia-cuda
    elif [[ "$FAMILIA" == *arch* || "$ID" == arch ]]; then
      instalar_pacotes nvidia nvidia-utils
    else
      aviso "Não sei instalar o driver NVIDIA nesta distribuição; seguindo (o assistente cai para CPU se a GPU não funcionar)."
    fi
    if ! nvidia-smi >/dev/null 2>&1; then
      continuar_apos_reiniciar "$@"
    fi
  fi
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader || true
fi

# ---------------------------------------------------------------- Docker
passo "Docker"
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  if [ $WSL = 1 ] && [ -e /mnt/c/Program\ Files/Docker/Docker/Docker\ Desktop.exe ]; then
    aviso "Docker Desktop instalado no Windows: ative a integração com esta distribuição WSL (Settings › Resources › WSL integration)."
  fi
  curl -fsSL https://get.docker.com | sh
fi
if command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then
  systemctl enable --now docker >/dev/null 2>&1 || true
else
  service docker start >/dev/null 2>&1 || true
fi
if [ "$USUARIO" != root ]; then usermod -aG docker "$USUARIO" || true; fi
docker compose version

# ---------------------------------------------------------------- NVIDIA Container Toolkit
if [ $TEM_NVIDIA = 1 ] && nvidia-smi >/dev/null 2>&1; then
  passo "NVIDIA Container Toolkit (GPU dentro do Docker)"
  if ! command -v nvidia-ctk >/dev/null; then
    if command -v apt-get >/dev/null; then
      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
      curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list
      instalar_pacotes nvidia-container-toolkit
    elif command -v dnf >/dev/null; then
      curl -fsSL https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo > /etc/yum.repos.d/nvidia-container-toolkit.repo
      instalar_pacotes nvidia-container-toolkit
    else
      instalar_pacotes nvidia-container-toolkit
    fi
  fi
  if ! docker info 2>/dev/null | grep -qi 'nvidia'; then
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker 2>/dev/null || service docker restart
  fi
fi

# ---------------------------------------------------------------- resto (Python)
passo "Instalando e configurando o assistente"
python3 scripts/instalar.py "$@"
