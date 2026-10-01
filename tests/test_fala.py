"""Testes do serviço de fala: limpeza de texto pt-BR e API no formato OpenAI (motores simulados)."""
from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "servicos" / "fala"))
texto_pt = importlib.import_module("texto_pt")


@pytest.mark.parametrize(
    "entrada, esperado",
    [
        ("Custa R$ 10,50 hoje.", "Custa dez reais e cinquenta centavos hoje."),
        ("R$ 1.200", "mil e duzentos reais"),
        ("Subiu 25% em 2025.", "Subiu 25 por cento em 2025."),
        ("Faz 30°C lá fora", "Faz 30 graus Celsius lá fora"),
        ("Às 14h30 com o Dr. Silva", "Às 14 horas e 30 com o doutor Silva"),
        ("## Título\n- **um** item\n- outro 😀", "Título\num item\noutro"),
        ("Veja [este site](https://exemplo.com) ou https://a.b/c", "Veja este site ou o link"),
        ("<think>raciocínio</think>Resposta.", "Resposta."),
        ("Código:\n```python\nprint(1)\n```\nFim.", "Código:\nFim."),
    ],
)
def test_preparar_para_fala(entrada, esperado):
    assert texto_pt.preparar_para_fala(entrada) == esperado


def test_dividir_frases_quebra_textos_longos():
    longo = "Esta é uma frase, " * 30 + "fim."
    partes = texto_pt.dividir_frases("Oi. Tudo bem? " + longo)
    assert partes[:2] == ["Oi.", "Tudo bem?"]
    assert all(len(p) <= 220 for p in partes)
    assert "".join(partes).replace(" ", "") == ("Oi.Tudobem?" + longo).replace(" ", "")


@pytest.mark.parametrize(
    "texto, alucinacao",
    [
        ("Legendas pela comunidade Amara.org", True),
        ("Obrigado por assistir!", True),
        ("...", True),
        ("[Música]", True),
        ("Toca uma música para mim", False),
        ("E aí, tudo bem?", False),
        ("Qual a previsão do tempo para amanhã?", False),
    ],
)
def test_alucinacoes(texto, alucinacao):
    assert texto_pt.eh_alucinacao(texto) is alucinacao


class STTFalso:
    info = {"estado": "pronto", "motor": "falso"}

    def transcrever(self, audio, idioma, prompt):
        dados = audio.read()
        assert dados.startswith(b"RIFF")
        return {"text": "olá mundo", "language": idioma, "duration": 1.0, "processing_time": 0.01}


class TTSFalso:
    info = {"estado": "pronto", "motor": "falso"}
    kokoro = object()

    def sintetizar(self, texto, voz, velocidade):
        self.ultimo = (texto, voz, velocidade)
        t = np.linspace(0, 1, 24000, dtype=np.float32)
        return 0.2 * np.sin(2 * np.pi * 220 * t), 24000, "falso"


@pytest.fixture()
def cliente():
    sys.modules.pop("app", None)
    app_mod = importlib.import_module("app")
    app_mod.stt = STTFalso()
    app_mod.tts = TTSFalso()
    for ev in app_mod.pronto.values():
        ev.set()
    yield TestClient(app_mod.app), app_mod
    sys.modules.pop("app", None)


def _wav() -> bytes:
    buf = io.BytesIO()
    sf.write(buf, np.zeros(16000, dtype=np.float32), 16000, format="WAV")
    return buf.getvalue()


def test_transcricao_formato_openai(cliente):
    c, _ = cliente
    r = c.post("/v1/audio/transcriptions", files={"file": ("a.wav", _wav(), "audio/wav")}, data={"model": "whisper-1", "language": "pt-BR"})
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "olá mundo"}
    r = c.post("/v1/audio/transcriptions", files={"file": ("a.wav", _wav())}, data={"response_format": "text"})
    assert r.text == "olá mundo"


@pytest.mark.parametrize("formato, mime, magica", [("mp3", "audio/mpeg", b"\xff"), ("wav", "audio/wav", b"RIFF"), ("opus", "audio/ogg", b"OggS"), ("flac", "audio/flac", b"fLaC")])
def test_sintese_formatos(cliente, formato, mime, magica):
    c, mod = cliente
    r = c.post("/v1/audio/speech", json={"model": "tts-1", "input": "Olá!", "voice": "alloy", "response_format": formato, "speed": 1.1})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(mime)
    assert r.content[:4].startswith(magica)
    assert mod.tts.ultimo == ("Olá!", "alloy", 1.1)


def test_mp3_e_o_padrao(cliente):
    c, _ = cliente
    r = c.post("/v1/audio/speech", json={"input": "Teste"})
    assert r.headers["content-type"] == "audio/mpeg"


def test_vozes_e_saude(cliente):
    c, _ = cliente
    ids = [v["id"] for v in c.get("/v1/audio/voices").json()["voices"]]
    assert {"pf_dora", "pm_alex", "pm_santa", "pt_BR-faber-medium"} <= set(ids)
    assert c.get("/health").json()["status"] == "ok"
