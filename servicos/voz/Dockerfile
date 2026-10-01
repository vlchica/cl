# Estágio 1: arquivos do Silero VAD e do ONNX Runtime Web servidos localmente (funciona offline)
FROM node:22-slim AS ativos
WORKDIR /ativos
RUN npm install --no-audit --no-fund --omit=dev @ricky0123/vad-web@0.0.31 onnxruntime-web@1.30.0 \
 && mkdir -p /saida \
 && cp node_modules/@ricky0123/vad-web/dist/bundle.min.js \
       node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js \
       node_modules/@ricky0123/vad-web/dist/silero_vad_v5.onnx \
       node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx \
       node_modules/onnxruntime-web/dist/ort.wasm.min.js \
       node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm \
       node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.mjs \
       /saida/

# Estágio 2: servidor
FROM python:3.12-slim-bookworm
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends curl tini && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .
COPY static ./static
COPY --from=ativos /saida ./static/vad
ENV CONVERSAS_DIR=/dados/conversas
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fs http://localhost:8000/health || exit 1
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning", "--ws-max-size", "33554432"]
