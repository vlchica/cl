ARG IMAGEM_BASE=assistente/base:cu126
FROM ${IMAGEM_BASE}

# Versão fixa do ComfyUI (interface estável e API usada pelo Open WebUI e pelas ferramentas)
ARG COMFYUI_VERSAO=v0.38.0
RUN git clone --depth 1 --branch "${COMFYUI_VERSAO}" https://github.com/comfyanonymous/ComfyUI.git /comfyui \
 && pip install -r /comfyui/requirements.txt

WORKDIR /comfyui
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8188
HEALTHCHECK --interval=30s --timeout=10s --start-period=1800s --retries=3 \
  CMD curl -fs http://localhost:8188/system_stats || exit 1
ENTRYPOINT ["tini", "--", "/usr/local/bin/entrypoint.sh"]
