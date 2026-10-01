ARG IMAGEM_BASE=assistente/base:cu126
FROM ${IMAGEM_BASE}

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

ENV MODELOS_DIR=/modelos \
    HF_HOME=/modelos/hf \
    NIVEL_LOG=INFO
COPY app.py texto_pt.py ./

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=900s --retries=3 \
  CMD curl -fs http://localhost:8000/health || exit 1
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"]
