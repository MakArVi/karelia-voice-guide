"""Веб-сервер мини-приложения: статика + API для вопросов голосом и текстом."""
from __future__ import annotations

import logging
import secrets
from collections import OrderedDict

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import tts
from .config import WEBAPP_DIR, settings
from .core import assistant, stt
from .knowledge import Answer, kb
from .telegram_auth import validate_init_data

log = logging.getLogger(__name__)
app = FastAPI(title="Голосовой путеводитель по Карелии", docs_url=None, redoc_url=None, openapi_url=None)

_speech: OrderedDict[str, str] = OrderedDict()  # ключ ссылки на озвучку → текст ответа
MAX_UPLOAD = 10 * 1024 * 1024


def current_user(request: Request) -> str:
    """Пользователь Telegram из подписанных initData; с самого компьютера — режим разработки."""
    init_data = request.headers.get("x-telegram-init-data", "")
    if init_data:
        user = validate_init_data(init_data, settings.bot_token)
        if user is None:
            raise HTTPException(401, "Не удалось проверить данные Telegram. Откройте путеводитель заново.")
        if not settings.is_allowed(user["id"]):
            raise HTTPException(403, "Этот путеводитель приватный.")
        return f"tg:{user['id']}"
    is_local = request.client and request.client.host in {"127.0.0.1", "::1"}
    if is_local and "cf-connecting-ip" not in request.headers:
        return "local"
    raise HTTPException(401, "Откройте путеводитель из Telegram.")


def _speech_url(text: str) -> str | None:
    """Текст отдаём сразу, а озвучку браузер подгрузит по ссылке — потоком, пока она синтезируется."""
    if not tts.speakable(text):
        return None
    key = secrets.token_urlsafe(12)
    _speech[key] = text
    if len(_speech) > 300:
        _speech.popitem(last=False)
    return f"/api/tts/{key}.mp3"


async def _payload(question: str, answer: Answer) -> dict:
    place = answer.place
    return {
        "question": question,
        "answer": answer.text,
        "place": {"id": place.id, "name": place.name, "lat": place.lat, "lon": place.lon} if place else None,
        "sources": answer.sources[:4],
        "audio_url": _speech_url(answer.speech),
        "mode": answer.mode,
    }


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class SpeakIn(BaseModel):
    place_id: str


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "stt": stt.ready, "mode": assistant.mode}


@app.get("/api/places")
async def places() -> list[dict]:
    return kb.public_places()


@app.post("/api/ask")
async def ask(body: AskIn, user: str = Depends(current_user)) -> dict:
    question = body.text.strip()
    return await _payload(question, await assistant.ask(user, question))


@app.post("/api/voice")
async def voice(audio: UploadFile = File(...), user: str = Depends(current_user)) -> dict:
    data = await audio.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Слишком длинная запись — уложитесь в полминуты.")
    if len(data) < 1000:
        return {"error": "Запись получилась пустой. Удерживайте микрофон чуть дольше."}
    try:
        question = await stt.transcribe(data)
    except Exception as e:  # noqa: BLE001
        log.exception("Ошибка распознавания")
        return {"error": f"Не получилось распознать запись: {e}"}
    if not question:
        return {"error": "Не расслышал вопрос. Попробуйте ещё раз — чуть громче и ближе к микрофону."}
    return await _payload(question, await assistant.ask(user, question))


@app.post("/api/speak")
async def speak(body: SpeakIn, user: str = Depends(current_user)) -> dict:
    if body.place_id not in kb.by_id:
        raise HTTPException(404, "Нет такого места")
    answer = assistant.about_place(user, body.place_id)
    return await _payload(f"Расскажи про {kb.by_id[body.place_id].name}", answer)


@app.get("/api/tts/{key}.mp3")
async def speech(key: str) -> Response:
    text = _speech.get(key)
    if text is None:
        raise HTTPException(404, "Озвучка устарела")
    headers = {"Cache-Control": "private, max-age=3600"}
    cached = tts.cached_mp3(text)
    if cached is not None:
        return Response(cached, media_type="audio/mpeg", headers=headers)

    async def chunks():
        try:
            async for chunk in tts.stream_mp3(text):
                yield chunk
        except Exception:  # noqa: BLE001
            log.exception("Не удалось озвучить ответ")

    return StreamingResponse(chunks(), media_type="audio/mpeg", headers=headers)


@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/index.html"} or request.url.path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
