"""Распознавание речи локально на этом компьютере (faster-whisper)."""
from __future__ import annotations

import asyncio
import io
import logging
import sys
import time
from pathlib import Path

from .config import MODELS_DIR, settings

log = logging.getLogger(__name__)

# Подсказка модели: названия мест распознаются заметно точнее
PROMPT = ("Вопрос гиду о Карелии: Кижи, Валаам, Рускеала, Кивач, Марциальные воды, Гирвас, Петрозаводск, "
          "Сортавала, Воттоваара, Паанаярви, Нуорунен, Ладожские шхеры, Онежское озеро, петроглифы, "
          "Беломорск, Залавруга, Бесов Нос.")

# Типичные «галлюцинации» Whisper на тишине
_HALLUCINATIONS = ("субтитр", "продолжение следует", "dimatorzok", "редактор субтитров", "спасибо за просмотр")


def decode_audio(data: bytes):
    """Любой формат (ogg/opus из Telegram, webm и m4a из браузера, mp3) → моно 16 кГц float32.

    Декодируем сами: встроенный в faster-whisper декодер не совместим с новыми версиями PyAV.
    """
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    chunks = []
    with av.open(io.BytesIO(data), mode="r") as container:
        for frame in container.decode(audio=0):
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def _ascii_path(path: Path) -> str:
    """CTranslate2 на Windows может не открыть путь с кириллицей — берём короткое имя 8.3."""
    path.mkdir(parents=True, exist_ok=True)
    text = str(path)
    if sys.platform != "win32" or text.isascii():
        return text
    import ctypes
    buffer = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetShortPathNameW(text, buffer, 1024) and buffer.value.isascii():
        return buffer.value
    return text


class SpeechToText:
    def __init__(self) -> None:
        self._model = None
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock()
        self.error: str | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    async def load(self) -> None:
        try:
            self._model = await asyncio.to_thread(self._load_sync)
        except Exception as e:  # noqa: BLE001 — бот должен работать и без распознавания
            self.error = f"{type(e).__name__}: {e}"
            log.exception("Не удалось загрузить модель распознавания речи")
        finally:
            self._ready.set()

    def _load_sync(self):
        from faster_whisper import WhisperModel

        started = time.perf_counter()
        log.info("Загружаю модель распознавания речи «%s» (первый запуск скачивает её)…", settings.whisper_model)
        model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute,
            cpu_threads=settings.whisper_threads,
            download_root=_ascii_path(MODELS_DIR),
        )
        log.info("Модель распознавания готова за %.1f с", time.perf_counter() - started)
        return model

    async def transcribe(self, audio: bytes) -> str:
        await self._ready.wait()
        if self._model is None:
            raise RuntimeError(f"распознавание речи недоступно ({self.error})")
        async with self._lock:
            return await asyncio.to_thread(self._transcribe_sync, audio)

    def _transcribe_sync(self, audio: bytes) -> str:
        started = time.perf_counter()
        samples = decode_audio(audio)
        if samples.size < 16000 * 0.3:  # меньше 0,3 с — пустая запись
            return ""
        segments, info = self._model.transcribe(
            samples,
            language="ru",
            beam_size=5,
            vad_filter=True,
            initial_prompt=PROMPT,
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if any(h in text.lower() for h in _HALLUCINATIONS) or text == PROMPT:
            text = ""
        log.info("Распознано за %.1f с (аудио %.1f с): %s", time.perf_counter() - started, info.duration, text or "—")
        return text
