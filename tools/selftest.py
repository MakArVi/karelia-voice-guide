"""Самопроверка без Telegram: база знаний, синтез и распознавание речи, API мини-приложения.

    .venv\\Scripts\\python tools\\selftest.py
"""
import asyncio
import hashlib
import hmac
import json
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402

from app import tts  # noqa: E402
from app.config import settings  # noqa: E402
from app.core import stt  # noqa: E402
from app.knowledge import kb  # noqa: E402
from app.server import app  # noqa: E402
from app.telegram_auth import validate_init_data  # noqa: E402


def make_init_data(user_id: int = 1, token: str = settings.bot_token) -> str:
    """Подписывает тестовые initData так же, как это делает Telegram."""
    fields = {"auth_date": str(int(time.time())), "query_id": "selftest",
              "user": json.dumps({"id": user_id, "first_name": "Тест"}, ensure_ascii=False)}
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print("✅" if ok else "❌", name, f"— {detail}" if detail else "")


async def main() -> None:
    print("— База знаний")
    for question, expected in [("Как добраться до Кижей?", "kizhi"), ("Сколько стоит вход в Рускеалу?", "ruskeala"),
                               ("Когда лучше ехать на Валаам?", "valaam"), ("Где смотреть петроглифы Залавруги?",
                                                                            "white_sea_petroglyphs")]:
        answer = kb.answer(question)
        check(f"«{question}»", answer.place and answer.place.id == expected, answer.text[:70] + "…")
    follow = kb.answer("а как туда добраться?", last_place_id="valaam")
    check("Уточняющий вопрос помнит место", follow.place and follow.place.id == "valaam")

    print("— Подпись данных Telegram")
    check("Верная подпись принимается", validate_init_data(make_init_data(), settings.bot_token) is not None)
    check("Поддельная подпись отклоняется",
          validate_init_data(make_init_data(token="1:fake"), settings.bot_token) is None)

    print("— Голос")
    started = time.perf_counter()
    mp3 = await tts.synthesize_mp3("Как добраться до Кижей из Петрозаводска?")
    check("Синтез речи", len(mp3) > 5000, f"{len(mp3)} байт за {time.perf_counter() - started:.1f} с")
    check("Голосовое сообщение OGG/Opus", tts.mp3_to_ogg_opus(mp3)[:4] == b"OggS")
    await stt.load()
    started = time.perf_counter()
    text = await stt.transcribe(mp3)
    check("Распознавание речи", "киж" in text.lower(), f"«{text}» за {time.perf_counter() - started:.1f} с")

    print("— API мини-приложения")
    client = TestClient(app)
    headers = {"X-Telegram-Init-Data": make_init_data()}
    response = client.get("/api/health")
    check("/api/health", response.status_code == 200, response.text)
    response = client.get("/api/places")
    check("/api/places", response.status_code == 200 and len(response.json()) >= 10, f"{len(response.json())} мест")
    response = client.post("/api/ask", json={"text": "Когда работает Кивач?"}, headers=headers)
    data = response.json()
    check("/api/ask", response.status_code == 200 and bool(data.get("audio_url")), data.get("answer", "")[:60] + "…")
    response = client.post("/api/voice", files={"audio": ("q.mp3", mp3, "audio/mpeg")}, headers=headers)
    data = response.json()
    check("/api/voice", response.status_code == 200 and bool(data.get("question")), data.get("question", data))
    response = client.get(data.get("audio_url") or "/api/tts/none.mp3")
    check("Озвучка ответа (поток MP3)", response.status_code == 200 and len(response.content) > 5000
          and response.headers["content-type"].startswith("audio/mpeg"), f"{len(response.content)} байт")
    response = client.post("/api/ask", json={"text": "тест"},
                           headers={"X-Telegram-Init-Data": make_init_data(token="1:fake")})
    check("Чужая подпись → 401", response.status_code == 401)
    response = client.get("/")
    check("Страница мини-приложения", response.status_code == 200 and "Карельский гид" in response.text)

    print(f"\nИтого: {sum(results)} из {len(results)} проверок пройдено")


if __name__ == "__main__":
    asyncio.run(main())
