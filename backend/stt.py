from __future__ import annotations

import tempfile
from pathlib import Path

_model = None


def _get_model():
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed. Run: pip install faster-whisper"
            ) from exc
        # 'tiny' = fastest, 'base' = better quality. CPU-only int8 is laptop-friendly.
        _model = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _model


def transcribe_bytes(audio_bytes: bytes, suffix: str = ".webm") -> str:
    """Transcribe a recorded audio blob and return the text."""
    model = _get_model()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)
    try:
        segments, _info = model.transcribe(str(tmp_path), beam_size=1)
        return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        tmp_path.unlink(missing_ok=True)
