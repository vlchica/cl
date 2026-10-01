#!/bin/sh
# Baixa (uma vez) o checkpoint escolhido pelo detector de hardware e inicia o ComfyUI.
set -e
MODELOS=/comfyui/models
mkdir -p "$MODELOS/checkpoints" "$MODELOS/vae" "$MODELOS/loras" "$MODELOS/embeddings" /comfyui/output /comfyui/input

case "${SD_MODELO:-sdxl}" in
  sdxl)
    ARQUIVO=sd_xl_base_1.0.safetensors
    URL=https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors ;;
  sd15)
    ARQUIVO=v1-5-pruned-emaonly-fp16.safetensors
    URL=https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors ;;
  *)
    ARQUIVO= ;;
esac

if [ -n "$ARQUIVO" ] && [ ! -s "$MODELOS/checkpoints/$ARQUIVO" ]; then
  echo "Baixando $ARQUIVO (só na primeira vez)..."
  tentativa=1
  until curl -fL --retry 5 --retry-delay 5 -C - -o "$MODELOS/checkpoints/$ARQUIVO.part" "$URL"; do
    tentativa=$((tentativa + 1))
    [ "$tentativa" -gt 5 ] && { echo "Falha ao baixar $ARQUIVO"; exit 1; }
    sleep 10
  done
  mv "$MODELOS/checkpoints/$ARQUIVO.part" "$MODELOS/checkpoints/$ARQUIVO"
fi

# shellcheck disable=SC2086
exec python main.py --listen 0.0.0.0 --port 8188 --disable-auto-launch --preview-method none ${COMFYUI_ARGS:-}
