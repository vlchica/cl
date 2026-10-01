"""Serviço de fala: reconhecimento (faster-whisper) e síntese (Kokoro, com Piper de reserva).

Expõe a mesma API do OpenAI, para o Open WebUI e o modo de voz usarem sem adaptação:
    POST /v1/audio/transcriptions   (multipart: file, language, prompt, response_format)
    POST /v1/audio/speech           (json: input, voice, speed, response_format)
    GET  /v1/audio/voices, /v1/audio/models, /v1/models, /health

Cada motor tenta várias configurações em ordem e fica com a primeira que
funcionar (GPU → CPU → modelo menor; Kokoro → Piper), registrando o motivo.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from texto_pt import dividir_frases, eh_alucinacao, preparar_para_fala

log = logging.getLogger("fala")
logging.basicConfig(level=os.getenv("NIVEL_LOG", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

MODELOS = Path(os.getenv("MODELOS_DIR", "/modelos"))
WHISPER_MODELO = os.getenv("WHISPER_MODELO", "large-v3-turbo")
WHISPER_DISPOSITIVO = os.getenv("WHISPER_DISPOSITIVO", "cuda")
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8_float16")
WHISPER_IDIOMA = os.getenv("WHISPER_IDIOMA", "pt")
WHISPER_BEAM = int(os.getenv("WHISPER_BEAM", "5" if WHISPER_DISPOSITIVO == "cuda" else "1"))
WHISPER_PROMPT = os.getenv(
    "WHISPER_PROMPT", "Conversa em português do Brasil, com pontuação e acentuação corretas."
)
TTS_DISPOSITIVO = os.getenv("TTS_DISPOSITIVO", "cpu")
TTS_VOZ = os.getenv("TTS_VOZ", "pf_dora")
PIPER_VOZ = os.getenv("PIPER_VOZ", "pt_BR-faber-medium")
PIPER_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/{nome}/{qualidade}/"
    "pt_BR-{nome}-{qualidade}.onnx{ext}?download=true"
)

VOZES_KOKORO = {
    "pf_dora": "Dora (feminina, Kokoro)",
    "pm_alex": "Alex (masculina, Kokoro)",
    "pm_santa": "Santa (masculina, Kokoro)",
}
# Nomes de voz do OpenAI caem na voz pt-BR mais próxima
APELIDOS_VOZ = {
    "alloy": "pf_dora", "nova": "pf_dora", "shimmer": "pf_dora", "coral": "pf_dora", "sage": "pf_dora",
    "echo": "pm_alex", "onyx": "pm_alex", "ash": "pm_alex", "ballad": "pm_alex", "fable": "pm_santa",
    "verse": "pm_alex", "feminina": "pf_dora", "masculina": "pm_alex",
}
TIPOS_AUDIO = {
    "mp3": ("MP3", "MPEG_LAYER_III", "audio/mpeg"),
    "wav": ("WAV", "PCM_16", "audio/wav"),
    "flac": ("FLAC", "PCM_16", "audio/flac"),
    "opus": ("OGG", "OPUS", "audio/ogg"),
}


def _vram_usada_mb() -> int | None:
    """Memória de vídeo ocupada na GPU deste contêiner (para medir o custo de cada modelo)."""
    try:
        import pynvml

        pynvml.nvmlInit()
        # Com CUDA_DEVICE_ORDER=PCI_BUS_ID o índice do CUDA coincide com o do NVML
        indice = int((os.getenv("CUDA_VISIBLE_DEVICES") or "0").split(",")[0])
        h = pynvml.nvmlDeviceGetHandleByIndex(indice)
        return int(pynvml.nvmlDeviceGetMemoryInfo(h).used / 1024**2)
    except Exception:  # noqa: BLE001
        return None


class Reconhecedor:
    def __init__(self) -> None:
        self.modelo = None
        self.info: dict = {"estado": "carregando"}
        self.lock = threading.Lock()

    def carregar(self) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            self.info = {"estado": "erro", "falhas": [f"faster-whisper não instalado: {e}"]}
            return
        tentativas = [(WHISPER_MODELO, WHISPER_DISPOSITIVO, WHISPER_COMPUTE)]
        if WHISPER_DISPOSITIVO == "cuda":
            tentativas.append((WHISPER_MODELO, "cuda", "float16"))
            tentativas.append((WHISPER_MODELO, "cpu", "int8"))
        if WHISPER_MODELO not in ("small", "base", "tiny"):
            tentativas.append(("small", "cpu", "int8"))
        falhas = []
        for modelo, disp, compute in tentativas:
            antes = _vram_usada_mb() if disp == "cuda" else None
            try:
                t0 = time.time()
                m = WhisperModel(modelo, device=disp, compute_type=compute, download_root=str(MODELOS / "whisper"))
                # Aquecimento: a primeira inferência inicializa os kernels da GPU
                list(m.transcribe(np.zeros(16000, dtype=np.float32), language=WHISPER_IDIOMA, beam_size=1)[0])
                depois = _vram_usada_mb() if disp == "cuda" else None
                self.modelo = m
                self.info = {
                    "estado": "pronto",
                    "motor": "faster-whisper",
                    "modelo": modelo,
                    "dispositivo": disp,
                    "compute_type": compute,
                    "idioma": WHISPER_IDIOMA,
                    "segundos_para_carregar": round(time.time() - t0, 1),
                    "vram_mb": (depois - antes) if (antes is not None and depois is not None) else None,
                    "falhas_anteriores": falhas,
                }
                log.info("Whisper pronto: %s", self.info)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("Whisper %s/%s/%s falhou: %s", modelo, disp, compute, e)
                falhas.append(f"{modelo}/{disp}/{compute}: {e}")
        self.info = {"estado": "erro", "falhas": falhas}

    def transcrever(self, audio, idioma: str | None, prompt: str | None) -> dict:
        if self.modelo is None:
            raise RuntimeError("reconhecimento de voz indisponível")
        with self.lock:
            t0 = time.time()
            segmentos, info = self.modelo.transcribe(
                audio,
                language=idioma or WHISPER_IDIOMA or None,
                beam_size=WHISPER_BEAM,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
                condition_on_previous_text=False,
                initial_prompt=prompt or WHISPER_PROMPT,
            )
            partes = []
            for s in segmentos:
                # Trechos que o próprio Whisper acha que não são fala são descartados
                if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
                    continue
                partes.append(s.text.strip())
            texto = " ".join(p for p in partes if p).strip()
            if texto and eh_alucinacao(texto):
                log.info("Descartado como alucinação: %r", texto)
                texto = ""
            return {
                "text": texto,
                "language": info.language,
                "duration": round(info.duration, 2),
                "processing_time": round(time.time() - t0, 3),
            }


class Sintetizador:
    def __init__(self) -> None:
        self.kokoro = None
        self.piper = None
        self.info: dict = {"estado": "carregando"}
        self.lock = threading.Lock()

    def _carregar_kokoro(self, dispositivo: str):
        from kokoro import KPipeline  # noqa: PLC0415 - import pesado, só ao carregar

        antes = _vram_usada_mb() if dispositivo == "cuda" else None
        p = KPipeline(lang_code="p", repo_id="hexgrad/Kokoro-82M", device=dispositivo)
        for voz in VOZES_KOKORO:
            p.load_voice(voz)
        list(p("Olá.", voice=TTS_VOZ))  # aquecimento e teste do espeak-ng pt-br
        depois = _vram_usada_mb() if dispositivo == "cuda" else None
        vram = (depois - antes) if (antes is not None and depois is not None) else None
        return p, vram

    def _carregar_piper(self):
        from piper import PiperVoice

        pasta = MODELOS / "piper"
        pasta.mkdir(parents=True, exist_ok=True)
        _, nome, qualidade = PIPER_VOZ.split("-", 2)
        onnx = pasta / f"{PIPER_VOZ}.onnx"
        for ext in ("", ".json"):
            destino = Path(f"{onnx}{ext}")
            if not destino.exists():
                url = PIPER_URL.format(nome=nome, qualidade=qualidade, ext=ext)
                log.info("Baixando voz Piper %s", destino.name)
                urllib.request.urlretrieve(url, f"{destino}.part")
                os.replace(f"{destino}.part", destino)
        return PiperVoice.load(str(onnx))

    def carregar(self) -> None:
        falhas = []
        dispositivos = [TTS_DISPOSITIVO] + (["cpu"] if TTS_DISPOSITIVO == "cuda" else [])
        for disp in dispositivos:
            try:
                t0 = time.time()
                self.kokoro, vram = self._carregar_kokoro(disp)
                self.info = {
                    "estado": "pronto",
                    "motor": "kokoro",
                    "dispositivo": disp,
                    "voz_padrao": TTS_VOZ,
                    "segundos_para_carregar": round(time.time() - t0, 1),
                    "vram_mb": vram,
                    "falhas_anteriores": falhas,
                }
                break
            except Exception as e:  # noqa: BLE001
                log.warning("Kokoro em %s falhou: %s", disp, e)
                falhas.append(f"kokoro/{disp}: {e}")
        # A voz Piper é baixada sempre (é pequena) para servir de reserva imediata
        try:
            self.piper = self._carregar_piper()
        except Exception as e:  # noqa: BLE001
            log.warning("Piper falhou: %s", e)
            falhas.append(f"piper: {e}")
        if self.kokoro is None:
            if self.piper is not None:
                self.info = {"estado": "pronto", "motor": "piper", "voz": PIPER_VOZ, "dispositivo": "cpu",
                             "vram_mb": 0, "falhas_anteriores": falhas}
            else:
                self.info = {"estado": "erro", "falhas": falhas}
        log.info("Síntese de voz: %s", self.info)

    def _com_kokoro(self, frases: list[str], voz: str, velocidade: float) -> tuple[np.ndarray, int]:
        voz = voz if voz in VOZES_KOKORO else TTS_VOZ
        pedacos = []
        for r in self.kokoro("\n".join(frases), voice=voz, speed=velocidade, split_pattern=r"\n+"):
            if r.audio is not None:
                pedacos.append(r.audio.detach().cpu().numpy())
        return (np.concatenate(pedacos) if pedacos else np.zeros(2400, dtype=np.float32)), 24000

    def _com_piper(self, frases: list[str], velocidade: float) -> tuple[np.ndarray, int]:
        from piper import SynthesisConfig

        cfg = SynthesisConfig(length_scale=1.0 / max(velocidade, 0.25))
        pedacos, taxa = [], 22050
        for chunk in self.piper.synthesize(" ".join(frases), syn_config=cfg):
            taxa = chunk.sample_rate
            pedacos.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16).astype(np.float32) / 32768.0)
        return (np.concatenate(pedacos) if pedacos else np.zeros(2205, dtype=np.float32)), taxa

    def sintetizar(self, texto: str, voz: str, velocidade: float) -> tuple[np.ndarray, int, str]:
        frases = dividir_frases(preparar_para_fala(texto))
        if not frases:
            return np.zeros(2400, dtype=np.float32), 24000, "vazio"
        voz = APELIDOS_VOZ.get(voz, voz)
        with self.lock:
            if self.kokoro is not None and not voz.startswith("pt_BR-"):
                try:
                    audio, taxa = self._com_kokoro(frases, voz, velocidade)
                    return audio, taxa, "kokoro"
                except Exception as e:  # noqa: BLE001
                    log.exception("Kokoro falhou nesta frase, usando Piper: %s", e)
            if self.piper is None:
                self.piper = self._carregar_piper()
            audio, taxa = self._com_piper(frases, velocidade)
            return audio, taxa, "piper"


def codificar(audio: np.ndarray, taxa: int, formato: str) -> tuple[bytes, str]:
    if formato == "pcm":
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
        return pcm, f"audio/L16;rate={taxa};channels=1"
    fmt, sub, mime = TIPOS_AUDIO.get(formato, TIPOS_AUDIO["mp3"])
    if fmt == "OGG" and taxa not in (8000, 12000, 16000, 24000, 48000):
        # Opus só aceita algumas taxas de amostragem
        novo = 24000
        x = np.linspace(0, len(audio) - 1, int(len(audio) * novo / taxa))
        audio, taxa = np.interp(x, np.arange(len(audio)), audio).astype(np.float32), novo
    buf = io.BytesIO()
    sf.write(buf, audio, taxa, format=fmt, subtype=sub)
    return buf.getvalue(), mime


stt = Reconhecedor()
tts = Sintetizador()
pronto = {"stt": threading.Event(), "tts": threading.Event()}


def _carregar_tudo() -> None:
    # Em sequência, para a medição de VRAM de cada modelo não se misturar
    try:
        stt.carregar()
    finally:
        pronto["stt"].set()
    try:
        tts.carregar()
    finally:
        pronto["tts"].set()


@asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI):
    threading.Thread(target=_carregar_tudo, daemon=True).start()
    yield


app = FastAPI(title="Fala (STT + TTS pt-BR)", version="1.0", lifespan=_ciclo_de_vida)


async def _esperar(nome: str, segundos: float = 900) -> None:
    if not await asyncio.to_thread(pronto[nome].wait, segundos):
        raise HTTPException(503, f"{nome} ainda carregando")


@app.get("/health")
def saude() -> JSONResponse:
    ok = stt.info.get("estado") == "pronto" and tts.info.get("estado") == "pronto"
    carregando = "carregando" in (stt.info.get("estado"), tts.info.get("estado"))
    estado = "ok" if ok else ("carregando" if carregando else "erro")
    return JSONResponse({"status": estado, "stt": stt.info, "tts": tts.info}, status_code=200 if ok else 503)


@app.get("/v1/models")
@app.get("/v1/audio/models")
def modelos() -> dict:
    ids = [stt.info.get("modelo", WHISPER_MODELO), "kokoro", "piper", "tts-1", "whisper-1"]
    return {"object": "list", "data": [{"id": i, "object": "model"} for i in ids], "models": [{"id": i} for i in ids]}


@app.get("/v1/audio/voices")
def vozes() -> dict:
    lista = [{"id": k, "name": v} for k, v in VOZES_KOKORO.items()] if tts.kokoro is not None else []
    lista.append({"id": PIPER_VOZ, "name": "Faber (masculina, Piper)"})
    return {"voices": lista}


@app.post("/v1/audio/transcriptions")
async def transcrever(
    file: UploadFile = File(...),
    model: str = Form(""),
    language: str = Form(""),
    prompt: str = Form(""),
    response_format: str = Form("json"),
) -> Response:
    await _esperar("stt")
    dados = await file.read()
    if not dados:
        raise HTTPException(400, "arquivo de áudio vazio")
    idioma = (language or WHISPER_IDIOMA or "").split("-")[0].lower() or None
    try:
        r = await asyncio.to_thread(stt.transcrever, io.BytesIO(dados), idioma, prompt or None)
    except Exception as e:  # noqa: BLE001
        log.exception("Falha na transcrição")
        raise HTTPException(500, f"falha na transcrição: {e}") from e
    if response_format == "text":
        return PlainTextResponse(r["text"])
    if response_format == "verbose_json":
        return JSONResponse({**r, "task": "transcribe"})
    return JSONResponse({"text": r["text"]})


@app.post("/v1/audio/speech")
async def falar(request: Request) -> Response:
    await _esperar("tts")
    try:
        corpo = await request.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "JSON inválido") from e
    texto = str(corpo.get("input", "")).strip()
    if not texto:
        raise HTTPException(400, "campo 'input' vazio")
    voz = str(corpo.get("voice") or TTS_VOZ)
    velocidade = float(corpo.get("speed") or 1.0)
    formato = str(corpo.get("response_format") or "mp3").lower()
    t0 = time.time()
    try:
        audio, taxa, motor = await asyncio.to_thread(tts.sintetizar, texto, voz, velocidade)
        dados, mime = await asyncio.to_thread(codificar, audio, taxa, formato)
    except Exception as e:  # noqa: BLE001
        log.exception("Falha na síntese")
        raise HTTPException(500, f"falha na síntese: {e}") from e
    return Response(
        dados,
        media_type=mime,
        headers={
            "X-Motor-TTS": motor,
            "X-Tempo-Sintese": f"{time.time() - t0:.3f}",
            "X-Duracao-Audio": f"{len(audio) / taxa:.2f}",
        },
    )
