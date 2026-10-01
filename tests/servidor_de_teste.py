"""Sobe o modo de voz ligado aos simuladores (para testar a página no navegador sem GPU).

    python tests/servidor_de_teste.py 8765 [segundos_de_audio_por_frase]
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import simuladores  # noqa: E402
import uvicorn  # noqa: E402

porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
simuladores.app.state.duracao_tts = float(sys.argv[2]) if len(sys.argv) > 2 else 0.1
with simuladores.ServidorEmThread() as sim:
    base = f"http://127.0.0.1:{sim.porta}"
    os.environ.update({"LLM_URL": f"{base}/v1", "LLM_MODELO": "teste", "FALA_URL": f"{base}/v1",
                       "FERRAMENTAS_URL": base, "NO_PROXY": "127.0.0.1,localhost", "CONVERSAS_DIR": "/tmp/conversas-teste"})
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "servicos" / "voz"))
    from app import app  # noqa: E402

    uvicorn.run(app, host="127.0.0.1", port=porta, log_level="warning")
