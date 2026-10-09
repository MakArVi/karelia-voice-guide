"""Общие сервисы для бота и веб-сервера."""
from .assistant import Assistant
from .stt import SpeechToText

assistant = Assistant()
stt = SpeechToText()
