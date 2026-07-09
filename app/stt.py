"""Local speech-to-text via faster-whisper. Lazily loads the model on first
use (model weights are downloaded from Hugging Face once, then cached
offline), so app startup stays fast even if transcription is never used."""

import logging
import tempfile
from pathlib import Path

from app.config import settings

logger = logging.getLogger("zylebot.stt")

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        logger.info(
            "Loading Whisper model %s (device=%s, compute_type=%s)...",
            settings.whisper_model_size,
            settings.whisper_device,
            settings.whisper_compute_type,
        )
        _model = WhisperModel(
            settings.whisper_model_size,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
        logger.info("Whisper model loaded.")
    return _model


def transcribe(audio_bytes: bytes, suffix: str = ".webm") -> str:
    """Transcribe recorded audio bytes to text. Blocking — call via
    asyncio.to_thread from async code."""
    model = _get_model()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = Path(f.name)
    try:
        segments, _info = model.transcribe(str(tmp_path), vad_filter=True)
        return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        tmp_path.unlink(missing_ok=True)
