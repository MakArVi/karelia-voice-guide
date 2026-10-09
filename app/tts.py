"""Синтез речи: голоса Microsoft через edge-tts. MP3 для мини-приложения, OGG/Opus для голосовых в чате."""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
from collections import OrderedDict

import edge_tts

from .config import settings

log = logging.getLogger(__name__)

_URL = re.compile(r"https?://\S+|\b[\w.-]+\.(?:ru|com|org|рф)\b/?\S*", re.IGNORECASE)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_MARKUP = re.compile(r"[*_#`\[\]<>|]")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
_cache: OrderedDict[str, bytes] = OrderedDict()


def speakable(text: str) -> str:
    """Текст для озвучки: без ссылок, эмодзи, разметки и координат."""
    text = _URL.sub("", text)
    text = _EMOJI.sub("", text)
    text = _MARKUP.sub("", text)
    sentences = [s for s in _SENTENCE.split(text) if "оординат" not in s]
    text = " ".join(sentences)
    text = re.sub(r"\s+([,.:;!?])", r"\1", text)
    text = re.sub(r"[:,]\s*([.!?])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _cache_key(text: str) -> str:
    return hashlib.sha1(f"{settings.tts_voice}|{settings.tts_rate}|{text}".encode()).hexdigest()


def cached_mp3(text: str) -> bytes | None:
    key = _cache_key(speakable(text))
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    return None


async def stream_mp3(text: str):
    """Отдаёт MP3 кусками по мере синтеза — мини-приложение начинает играть почти сразу."""
    text = speakable(text)
    if not text:
        raise ValueError("пустой текст для озвучки")
    communicate = edge_tts.Communicate(text, settings.tts_voice, rate=settings.tts_rate)
    audio = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
            yield chunk["data"]
    if not audio:
        raise RuntimeError("синтез речи вернул пустой звук")
    _cache[_cache_key(text)] = bytes(audio)
    if len(_cache) > 200:
        _cache.popitem(last=False)


async def synthesize_mp3(text: str) -> bytes:
    cached = cached_mp3(text)
    if cached is not None:
        return cached
    audio = bytearray()
    async for chunk in stream_mp3(text):
        audio.extend(chunk)
    return bytes(audio)


def mp3_to_ogg_opus(mp3: bytes) -> bytes:
    """Голосовые сообщения Telegram — это OGG с кодеком Opus."""
    import av

    source = av.open(io.BytesIO(mp3), format="mp3")
    out_buffer = io.BytesIO()
    target = av.open(out_buffer, mode="w", format="ogg")
    stream = target.add_stream("libopus", rate=48000, layout="mono")
    stream.bit_rate = 32000
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)
    fifo = av.AudioFifo()
    frame_size = 960  # 20 мс при 48 кГц

    def encode_ready(flush: bool = False) -> None:
        while fifo.samples >= frame_size or (flush and fifo.samples):
            frame = fifo.read(min(frame_size, fifo.samples) if flush else frame_size)
            if frame.samples < frame_size:  # последний кусок дополняем тишиной
                padded = av.AudioFrame(format="s16", layout="mono", samples=frame_size)
                padded.planes[0].update(bytes(frame.planes[0])[: frame.samples * 2].ljust(frame_size * 2, b"\0"))
                padded.sample_rate = 48000
                padded.pts = frame.pts
                frame = padded
            for packet in stream.encode(frame):
                target.mux(packet)

    for frame in source.decode(audio=0):
        for resampled in resampler.resample(frame):
            fifo.write(resampled)
        encode_ready()
    for resampled in resampler.resample(None):
        fifo.write(resampled)
    encode_ready(flush=True)
    for packet in stream.encode(None):
        target.mux(packet)
    target.close()
    source.close()
    return out_buffer.getvalue()


async def synthesize_voice(text: str) -> tuple[bytes, str]:
    """Возвращает (аудио, формат): 'ogg' для голосового сообщения или 'mp3', если конвертация не удалась."""
    mp3 = await synthesize_mp3(text)
    try:
        return await asyncio.to_thread(mp3_to_ogg_opus, mp3), "ogg"
    except Exception:  # noqa: BLE001
        log.exception("Не удалось перекодировать в OGG/Opus, отправлю MP3")
        return mp3, "mp3"
