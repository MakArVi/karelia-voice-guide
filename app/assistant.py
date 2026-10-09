"""Мозг гида: выбирает, кто отвечает (Claude или офлайн-база), и помнит контекст диалога."""
from __future__ import annotations

from collections import defaultdict, deque

from .config import settings
from .knowledge import Answer, kb
from .llm import ClaudeAnswerer


class Assistant:
    def __init__(self) -> None:
        self.claude = ClaudeAnswerer() if settings.llm_enabled else None
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=6))
        self._last_place: dict[str, str] = {}

    @property
    def mode(self) -> str:
        return "claude" if self.claude and not self.claude.disabled_reason else "offline"

    async def ask(self, user: str, question: str) -> Answer:
        question = question.strip()[:500]
        result = kb.answer(question, self._last_place.get(user))
        if self.claude:
            reply = await self.claude.ask(question, list(self._history[user]))
            if reply:
                text, place_id = reply
                place = kb.by_id.get(place_id) if place_id else result.place
                result = Answer(
                    text=text, speech=text, place=place, places=[place] if place else [],
                    sources=kb.source_refs(place.sources) if place else result.sources, mode="claude",
                )
        history = self._history[user]
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": result.speech})
        if result.place:
            self._last_place[user] = result.place.id
        return result

    def about_place(self, user: str, place_id: str) -> Answer:
        """Рассказ о месте по кнопке (без нейросети — мгновенно)."""
        self._last_place[user] = place_id
        return kb.place_answer(place_id)
