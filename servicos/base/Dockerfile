# Base comum dos serviços que usam PyTorch (fala e comfyui).
# Python 3.12 fixo (o Kokoro não suporta 3.13) e PyTorch do índice oficial
# escolhido pelo detector de hardware: cu126 (GPUs até RTX 40, inclusive
# Pascal/GTX 10), cu128 (RTX 50/Blackwell) ou cpu.
FROM python:3.12-slim-bookworm

ARG TORCH_INDICE=cu126
ARG TORCH_VERSAO=2.9.1
ARG TORCHVISION_VERSAO=0.24.1

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    CUDA_DEVICE_ORDER=PCI_BUS_ID

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git ffmpeg libsndfile1 espeak-ng libgl1 libglib2.0-0 tini \
 && rm -rf /var/lib/apt/lists/*

RUN pip install "torch==${TORCH_VERSAO}" "torchvision==${TORCHVISION_VERSAO}" "torchaudio==${TORCH_VERSAO}" \
      --index-url "https://download.pytorch.org/whl/${TORCH_INDICE}"

# cuBLAS e cuDNN instalados pelo pip junto com o PyTorch ficam visíveis para o
# CTranslate2 (faster-whisper) sem instalar o CUDA inteiro na imagem.
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:/usr/local/lib/python3.12/site-packages/nvidia/cuda_runtime/lib:/usr/local/lib/python3.12/site-packages/nvidia/cuda_nvrtc/lib

LABEL org.opencontainers.image.title="assistente-base" \
      org.opencontainers.image.description="Python 3.12 + PyTorch ${TORCH_VERSAO} (${TORCH_INDICE})"
