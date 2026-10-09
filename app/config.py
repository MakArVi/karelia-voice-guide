"""Настройки из файла .env (см. .env.example)."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value else default


@dataclass(frozen=True)
class Settings:
    bot_token: str = _env("BOT_TOKEN")
    # Ответы
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY")
    claude_model: str = _env("CLAUDE_MODEL", "claude-opus-5-5")
    claude_effort: str = _env("CLAUDE_EFFORT", "low")
    anthropic_proxy: str = _env("ANTHROPIC_PROXY")
    # Веб-сервер и туннель
    port: int = _int("PORT", 8080)
    public_url: str = _env("PUBLIC_URL").rstrip("/")
    tunnel: str = _env("TUNNEL", "cloudflared").lower()
    # Распознавание речи (faster-whisper)
    whisper_model: str = _env("WHISPER_MODEL", "small")
    whisper_device: str = _env("WHISPER_DEVICE", "cpu")
    whisper_compute: str = _env("WHISPER_COMPUTE", "int8")
    whisper_threads: int = _int("WHISPER_THREADS", 8)
    # Синтез речи (edge-tts)
    tts_voice: str = _env("TTS_VOICE", "ru-RU-SvetlanaNeural")
    tts_rate: str = _env("TTS_RATE", "+0%")
    # Доступ: пусто — пускаем всех, иначе id пользователей Telegram через запятую
    allowed_user_ids: frozenset[int] = field(default_factory=lambda: frozenset(
        int(x) for x in _env("ALLOWED_USER_IDS").replace(" ", "").split(",") if x
    ))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    def is_allowed(self, user_id: int | None) -> bool:
        return not self.allowed_user_ids or user_id in self.allowed_user_ids


settings = Settings()

DATA_DIR = ROOT / "data"
WEBAPP_DIR = ROOT / "webapp"
MODELS_DIR = ROOT / "models"
LOGS_DIR = ROOT / "logs"
BIN_DIR = ROOT / "bin"
