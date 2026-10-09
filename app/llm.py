"""Ответы через Claude — включаются, когда в .env задан ANTHROPIC_API_KEY.

Вся база знаний лежит в системном промпте (с кэшированием), поэтому модель отвечает
только по собранным фактам. При любой ошибке API возвращаем None, и гид отвечает офлайн.
"""
from __future__ import annotations

import logging
import re

import anthropic
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from .config import settings
from .knowledge import kb

log = logging.getLogger(__name__)

INSTRUCTIONS = """Ты — «Карельский гид», голосовой путеводитель по Республике Карелия в Telegram. Твой ответ озвучивает синтезатор речи, его слушают ушами.

Как отвечать:
- По-русски, дружелюбно и по делу. Начинай сам ответ сразу, без вступлений: это голосовой интерфейс, важна скорость.
- Коротко: 2–5 предложений, примерно до 70 слов. Если вопрос широкий — назови главное и предложи уточнить.
- Только обычный текст: без markdown, списков, эмодзи, ссылок и адресов сайтов. Числа пиши цифрами, единицы измерения — словами («километров», «рублей», «часов»).
- Опирайся только на факты из базы знаний ниже. Не выдумывай цены, расписания, расстояния и даты. Если нужного факта в базе нет — честно скажи об этом и посоветуй уточнить на официальном сайте места.
- Цены и расписания в базе взяты с официальных сайтов в начале октября 2026 года; называя их, можно добавить, что перед поездкой их стоит уточнить.
- Если спрашивают не о Карелии и не о путешествиях, вежливо скажи, что ты гид по Карелии, и предложи вопрос по теме.
- Учитывай предыдущие реплики: «туда», «там», «а цены?» относятся к месту, о котором говорили раньше.

Служебная метка: в самом конце ответа на отдельной строке напиши [place:ID], где ID — идентификатор места из базы, которому в основном посвящён ответ, или [place:none], если ответ не про конкретное место. Метку не озвучивают, она нужна, чтобы показать место на карте."""

_PLACE_TAG = re.compile(r"\[place:\s*([a-z_]+)\s*\]", re.IGNORECASE)


class ClaudeAnswerer:
    def __init__(self) -> None:
        kwargs: dict = {"api_key": settings.anthropic_api_key, "max_retries": 1, "timeout": 45.0}
        if settings.anthropic_proxy:
            kwargs["http_client"] = DefaultAsyncHttpxClient(proxy=settings.anthropic_proxy)
        self.client = AsyncAnthropic(**kwargs)
        self.system = [
            {"type": "text", "text": INSTRUCTIONS},
            {"type": "text", "text": kb.as_prompt_text(), "cache_control": {"type": "ephemeral"}},
        ]
        self.disabled_reason: str | None = None

    async def ask(self, question: str, history: list[dict]) -> tuple[str, str | None] | None:
        """Возвращает (текст, id места) или None — тогда используется офлайн-ответ."""
        if self.disabled_reason:
            return None
        try:
            response = await self.client.beta.messages.create(
                model=settings.claude_model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": settings.claude_effort},
                system=self.system,
                messages=[*history, {"role": "user", "content": question}],
            )
        except anthropic.AuthenticationError:
            self.disabled_reason = "неверный ANTHROPIC_API_KEY"
            log.error("Claude: ключ не принят — проверьте ANTHROPIC_API_KEY в .env. Пока отвечаю офлайн.")
            return None
        except anthropic.PermissionDeniedError as e:
            log.error("Claude: доступ запрещён (%s). Если API недоступен в вашем регионе, "
                      "укажите ANTHROPIC_PROXY в .env.", e.message)
            return None
        except anthropic.BadRequestError as e:
            log.error("Claude: некорректный запрос: %s", e.message)
            return None
        except anthropic.RateLimitError:
            log.warning("Claude: превышен лимит запросов, отвечаю офлайн")
            return None
        except anthropic.APIStatusError as e:
            log.error("Claude: ошибка API %s: %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError:
            log.warning("Claude: нет соединения с API, отвечаю офлайн")
            return None

        if response.stop_reason == "refusal":
            log.info("Claude отказался отвечать, использую офлайн-ответ")
            return None
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            return None
        match = _PLACE_TAG.search(text)
        place_id = match.group(1).lower() if match else None
        text = _PLACE_TAG.sub("", text).strip()
        usage = response.usage
        log.info("Claude (%s): %s вх. / %s вых. токенов, из кэша %s", response.model, usage.input_tokens,
                 usage.output_tokens, usage.cache_read_input_tokens)
        return text, (place_id if place_id in kb.by_id else None)
