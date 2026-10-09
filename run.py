"""Запуск: веб-сервер мини-приложения + туннель + Telegram-бот в одном процессе.

    .venv\\Scripts\\python run.py      (или двойной клик по start.bat)
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

import uvicorn

from app import bot as tgbot
from app.config import LOGS_DIR, settings
from app.core import assistant, stt
from app.server import app
from app.tunnel import CloudflaredTunnel

log = logging.getLogger("run")


class _Server(uvicorn.Server):
    """Ctrl+C обрабатываем сами, чтобы корректно остановить и бота, и туннель."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield

    def install_signal_handlers(self) -> None:
        pass


def _setup_logging() -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    file = logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8")
    file.setFormatter(logging.Formatter(fmt))
    logging.basicConfig(level=logging.INFO, handlers=[console, file])
    for noisy in ("aiogram.event", "httpx", "httpx2", "uvicorn.access", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def main() -> None:
    _setup_logging()
    if not settings.bot_token:
        sys.exit("В файле .env не задан BOT_TOKEN")

    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=settings.port, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    stt_task = asyncio.create_task(stt.load())
    while not server.started:
        if server_task.done():
            sys.exit(f"Не удалось запустить веб-сервер на порту {settings.port} — возможно, порт занят. "
                     "Поменяйте PORT в .env")
        await asyncio.sleep(0.1)
    log.info("Веб-сервер: http://127.0.0.1:%s", settings.port)

    public_url, tunnel = settings.public_url or None, None
    if not public_url and settings.tunnel == "cloudflared":
        tunnel = CloudflaredTunnel(settings.port)
        try:
            public_url = await tunnel.start()
        except Exception as e:  # noqa: BLE001 — без туннеля бот всё равно отвечает в чате
            log.warning("Туннель не запустился (%s). Мини-приложение недоступно, бот работает в чате.", e)
            tunnel.stop()
            tunnel = None

    bot, dp = tgbot.create_bot()
    await tgbot.setup_bot(bot, public_url)
    me = await bot.get_me()
    log.info("─" * 60)
    log.info("Бот @%s запущен", me.username)
    log.info("Мини-приложение: %s", public_url or "недоступно (нет туннеля)")
    log.info("Ответы: %s", "Claude (" + settings.claude_model + ")" if assistant.mode == "claude"
             else "база знаний гида (ключ Claude не задан)")
    log.info("Остановить: Ctrl+C")
    log.info("─" * 60)
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        log.info("Останавливаюсь…")
        await tgbot.reset_menu(bot)
        await bot.session.close()
        server.should_exit = True
        await asyncio.gather(server_task, return_exceptions=True)
        stt_task.cancel()
        if tunnel:
            tunnel.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
