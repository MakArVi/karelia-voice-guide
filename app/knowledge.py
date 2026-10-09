"""База знаний о Карелии и офлайн-поиск ответа (работает без нейросети)."""
from __future__ import annotations

import difflib
import json
import math
import re
from dataclasses import dataclass, field

import snowballstemmer

from .config import DATA_DIR

_stemmer = snowballstemmer.stemmer("russian")
_WORD = re.compile(r"[a-zа-я0-9]+")

STOPWORDS = {
    "а", "и", "в", "во", "на", "не", "но", "да", "ли", "же", "бы", "по", "о", "об", "от", "до", "из", "к", "ко",
    "с", "со", "у", "за", "для", "при", "про", "под", "над", "это", "этот", "эта", "эти", "то", "тот", "там",
    "тут", "туда", "сюда", "как", "что", "чем", "кто", "где", "мне", "меня", "мы", "вы", "ты", "я", "он", "она", "оно", "они",
    "его", "ее", "их", "мой", "твой", "ваш", "наш", "так", "уже", "еще", "или", "есть", "был", "была", "было",
    "были", "быть", "можно", "нужно", "ну", "вот", "очень", "какой", "какая", "какое", "какие", "расскажи",
    "расскажите", "скажи", "скажите", "пожалуйста", "подскажи", "подскажите", "хочу", "интересно", "знаешь",
}

SECTION_TITLES = {
    "overview": "Коротко",
    "what_to_see": "Что посмотреть",
    "history": "История",
    "how_to_get": "Как добраться",
    "season": "Когда ехать и режим работы",
    "prices": "Цены",
    "tips": "Советы",
    "facts": "Интересные факты",
}

# Намерения: (секция, сильные шаблоны, слабые шаблоны). Порядок важен при равном счёте.
INTENTS: list[tuple[str, list[str], list[str]]] = [
    ("how_to_get", [r"добра", r"доех", r"доплы", r"дойти", r"попаст", r"проех", r"добир", r"транспорт", r"поезд",
                    r"автобус", r"метеор", r"электрич", r"на машин", r"расстоян", r"далеко", r"маршрут до",
                    r"сколько (км|километр|ехать|часов|плыть)", r"катер", r"навигатор", r"координат",
                    r"как (туда )?(ехать|едут|попасть)"], []),
    ("prices", [r"сколько сто", r"стоимост", r"\bцен", r"билет", r"\bплат", r"\bдорог(о|ой|ая|ие)\b", r"бесплатн",
                r"рубл", r"льгот", r"скидк"], []),
    ("history", [r"истори", r"построен", r"построил", r"основа", r"\bсозда", r"возраст", r"сколько лет", r"легенд",
                 r"почему (так )?назва", r"назван", r"откуда", r"век[ае]?\b"], []),
    ("season", [r"сезон", r"режим", r"часы работы", r"во сколько", r"открыт", r"закрыт", r"работает", r"зимой",
                r"летом", r"весной", r"осенью", r"лучше (ехать|поехать|время)", r"навигац", r"погод"], [r"\bкогда\b"]),
    ("what_to_see", [r"что (там )?(посмотр|увидет|интересн|есть|делать|можно)", r"чем (интерес|знамен|примечат|известн|заня)",
                     r"достопримечат", r"экскурси", r"развлечен", r"посмотреть", r"увидеть"], [r"маршрут"]),
    ("tips", [r"совет", r"что взять", r"что надеть", r"одежд", r"опасн", r"клещ", r"\bзме", r"гадюк", r"правил",
              r"можно ли", r"нельзя", r"с собак", r"с детьми", r"разрешени", r"пропуск"], []),
    ("facts", [r"факт", r"интересное", r"удиви", r"загадк", r"необычн", r"секрет"], []),
]
_INTENTS = [(name, [re.compile(p) for p in strong], [re.compile(p) for p in weak]) for name, strong, weak in INTENTS]

FOLLOW_UP = re.compile(r"\b(туда|там|тут|туда же|это место|этого места|этом месте|оно|него|нее|неё|ним|ней|их)\b")


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return " ".join(_WORD.findall(text))


def stem(word: str) -> str:
    return _stemmer.stemWord(word)


def content_stems(text: str) -> list[str]:
    return [stem(w) for w in normalize(text).split() if w not in STOPWORDS]


@dataclass
class Place:
    id: str
    name: str
    title: str
    category: str
    emoji: str
    color: str
    lat: float
    lon: float
    location: str
    aliases: list[str]
    short: str
    sections: dict[str, str]
    sources: list[str]


@dataclass
class FaqItem:
    id: str
    questions: list[str]
    answer: str
    sources: list[str]
    stems: list[set[str]] = field(default_factory=list)


@dataclass
class Answer:
    text: str                      # для показа в чате
    speech: str                    # для озвучки
    place: Place | None = None
    places: list[Place] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    mode: str = "offline"


class KnowledgeBase:
    def __init__(self) -> None:
        self.sources: dict[str, dict] = {
            s["id"]: s for s in json.loads((DATA_DIR / "sources.json").read_text(encoding="utf-8"))
        }
        self.places: list[Place] = [
            Place(**p) for p in json.loads((DATA_DIR / "places.json").read_text(encoding="utf-8"))
        ]
        self.by_id = {p.id: p for p in self.places}
        self.faq: list[FaqItem] = [
            FaqItem(**f) for f in json.loads((DATA_DIR / "faq.json").read_text(encoding="utf-8"))
        ]
        docs = []
        for item in self.faq:
            item.stems = [set(content_stems(q)) for q in item.questions]
            docs.extend(item.stems)
        df: dict[str, int] = {}
        for d in docs:
            for s in d:
                df[s] = df.get(s, 0) + 1
        self._idf = {s: math.log(1 + len(docs) / n) for s, n in df.items()}
        self._idf_default = math.log(1 + len(docs))
        for item in self.faq + self.places:  # проверяем ссылки на источники при запуске
            for sid in item.sources:
                if sid not in self.sources:
                    raise ValueError(f"Неизвестный источник {sid!r} в {item.id}")

    # ---------- поиск мест и намерений ----------
    def find_places(self, text: str) -> list[tuple[Place, float]]:
        tokens = normalize(text).split()
        found = []
        for place in self.places:
            best = 0.0
            for alias in place.aliases:
                # «~слово» — общее слово (например, «петроглиф»): весит вдвое меньше точного названия
                weight = 0.5 if alias.startswith("~") else 1.0
                alias = alias.lstrip("~")
                parts = alias.split()
                for i in range(len(tokens) - len(parts) + 1):
                    if all(_token_matches(tokens[i + j], parts[j]) for j in range(len(parts))):
                        best = max(best, (len(alias.replace(" ", "")) + 2 * len(parts)) * weight)
                if len(parts) == 1 and len(parts[0]) >= 6:
                    for tok in tokens:
                        if len(tok) >= 6:
                            ratio = difflib.SequenceMatcher(None, tok[: len(parts[0]) + 1], parts[0]).ratio()
                            if ratio >= 0.84:
                                best = max(best, len(parts[0]) * ratio * 0.8 * weight)
            if best:
                found.append((place, best))
        found.sort(key=lambda x: -x[1])
        return found

    @staticmethod
    def detect_intents(text: str) -> list[str]:
        norm = normalize(text)
        scored = []
        for order, (name, strong, weak) in enumerate(_INTENTS):
            score = sum(1.0 for p in strong if p.search(norm)) + sum(0.5 for p in weak if p.search(norm))
            if score:
                scored.append((score, -order, name))
        scored.sort(reverse=True)
        result = [name for score, _, name in scored[:2] if score >= 1.0]
        if not result and scored:
            result = [scored[0][2]]
        return result

    def search_faq(self, text: str) -> tuple[FaqItem | None, float]:
        query = set(content_stems(text))
        if not query:
            return None, 0.0
        q_weight = sum(self._idf.get(s, self._idf_default) for s in query)
        best, best_score = None, 0.0
        for item in self.faq:
            for stems in item.stems:
                common = query & stems
                if not common:
                    continue
                w_common = sum(self._idf.get(s, self._idf_default) for s in common)
                w_doc = sum(self._idf.get(s, self._idf_default) for s in stems)
                score = w_common / math.sqrt(q_weight * w_doc)
                if score > best_score:
                    best, best_score = item, score
        return best, best_score

    # ---------- офлайн-ответ ----------
    def answer(self, question: str, last_place_id: str | None = None) -> Answer:
        places = self.find_places(question)
        intents = self.detect_intents(question)
        norm = normalize(question)
        about_region = "карел" in norm and not places

        if not places and last_place_id in self.by_id and not about_region:
            # «А как туда добраться?» — продолжаем разговор о последнем месте
            if FOLLOW_UP.search(norm) or (intents and self.search_faq(question)[1] < 0.6):
                places = [(self.by_id[last_place_id], 1.0)]

        if places:
            top = places[0][1]
            chosen = [p for p, s in places if s >= top * 0.75][:2]
            if len(chosen) > 1 and not intents:
                return self._compare(chosen)
            return self._about_place(chosen[0], intents)

        faq, faq_score = self.search_faq(question)
        if faq and faq_score >= 0.35:
            return Answer(text=faq.answer, speech=faq.answer, sources=self.source_refs(faq.sources))

        if intents:
            text = ("Уточните, пожалуйста, о каком месте вы спрашиваете. Я знаю про Кижи, Валаам, Рускеалу, "
                    "Кивач, Марциальные воды, петроглифы, Воттоваару, Паанаярви, Ладожские шхеры и Петрозаводск.")
        else:
            text = ("Я пока не нашёл ответа в своей базе. Я знаю про Кижи, Валаам, Рускеалу, Кивач, Марциальные "
                    "воды, петроглифы, Воттоваару, Паанаярви, Ладожские шхеры и Петрозаводск — спросите про них.")
        return Answer(text=text, speech=text)

    def place_answer(self, place_id: str) -> Answer:
        return self._about_place(self.by_id[place_id], [])

    def _about_place(self, place: Place, intents: list[str]) -> Answer:
        parts, missing = [], []
        for intent in intents:
            if intent in place.sections:
                parts.append(place.sections[intent])
            else:
                missing.append(intent)
        if not parts and missing:
            what = SECTION_TITLES[missing[0]].lower()
            text = (f"Про {what} для места «{place.name}» точных данных в моей базе нет. "
                    f"Вот что я знаю: {place.short}")
            return Answer(text=text, speech=text, place=place, places=[place],
                          sources=self.source_refs(place.sources))
        if not parts:
            speech = place.sections["overview"]
            text = speech + "\n\nСпросите ещё: что посмотреть, как добраться, когда ехать или сколько стоит."
            return Answer(text=text, speech=speech, place=place, places=[place],
                          sources=self.source_refs(place.sources))
        speech = " ".join(parts)
        return Answer(text=speech, speech=speech, place=place, places=[place],
                      sources=self.source_refs(place.sources))

    def _compare(self, places: list[Place]) -> Answer:
        lines = [f"{p.name}. {p.short}" for p in places]
        text = " ".join(lines) + " О каком месте рассказать подробнее?"
        sources = []
        for p in places:
            sources.extend(self.source_refs(p.sources))
        return Answer(text=text, speech=text, place=places[0], places=places, sources=_unique(sources))

    # ---------- вспомогательное ----------
    def source_refs(self, ids: list[str]) -> list[dict]:
        """Ссылки для показа под ответом: по одной на сайт."""
        refs, seen = [], set()
        for sid in ids:
            src = self.sources[sid]
            if src["label"] in seen:
                continue
            seen.add(src["label"])
            refs.append({"label": src["label"], "url": src["url"]})
        return refs

    def public_places(self) -> list[dict]:
        return [
            {
                "id": p.id, "name": p.name, "title": p.title, "category": p.category, "emoji": p.emoji,
                "color": p.color, "lat": p.lat, "lon": p.lon, "location": p.location, "short": p.short,
                "sources": self.source_refs(p.sources)[:3],
            }
            for p in self.places
        ]

    def as_prompt_text(self) -> str:
        """Вся база одним текстом — для системного промпта нейросети."""
        out = ["# База знаний «Карелия» (факты с официальных сайтов, собраны 07.10.2026)", ""]
        for p in self.places:
            out.append(f"## {p.title} [id: {p.id}]")
            out.append(f"Категория: {p.category}. Где: {p.location}. Координаты: {p.lat}, {p.lon}.")
            for key, title in SECTION_TITLES.items():
                if key in p.sections:
                    out.append(f"{title}: {p.sections[key]}")
            out.append("Источники: " + ", ".join(self.sources[s]["url"] for s in p.sources))
            out.append("")
        out.append("## Общие вопросы о Карелии")
        for item in self.faq:
            if item.id in {"greeting", "thanks", "help"}:
                continue
            out.append(f"Вопрос: {item.questions[0]}")
            out.append(f"Ответ: {item.answer}")
            out.append("")
        return "\n".join(out)


def _token_matches(token: str, alias_part: str) -> bool:
    if len(alias_part) >= 4:
        return token.startswith(alias_part)
    return token.startswith(alias_part) and len(token) <= len(alias_part) + 2


def _unique(refs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in refs:
        if r["label"] not in seen:
            seen.add(r["label"])
            out.append(r)
    return out


kb = KnowledgeBase()
