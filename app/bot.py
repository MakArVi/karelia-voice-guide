"""Telegram-бот: голосовые и текстовые вопросы прямо в чате, кнопка мини-приложения с картой."""
from __future__ import annotations

import html
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from aiogram.utils.chat_action import ChatActionSender

from . import tts
from .config import settings
from .core import assistant, stt
from .knowledge import Answer, kb

log = logging.getLogger(__name__)
router = Router()
PUBLIC_URL: str | None = None

SHORT_DESCRIPTION = "Голосовой путеводитель по Карелии: спросите голосом — отвечу голосом."
DESCRIPTION = ("Привет! Я голосовой гид по Карелии: Кижи, Валаам, Рускеала, Кивач, петроглифы и другие места. "
               "Отправьте голосовое сообщение с вопросом или откройте путеводитель с картой.")


def create_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML,
                                                                  link_preview_is_disabled=True))
    dp = Dispatcher()
    dp.include_router(router)
    return bot, dp


async def setup_bot(bot: Bot, public_url: str | None) -> None:
    global PUBLIC_URL
    PUBLIC_URL = public_url
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="places", description="Достопримечательности"),
        BotCommand(command="help", description="Как пользоваться"),
    ])
    if public_url:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Путеводитель",
                                                                    web_app=WebAppInfo(url=public_url)))
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    try:  # описание меняем только если оно другое — у Telegram жёсткие лимиты на эти вызовы
        if (await bot.get_my_short_description()).short_description != SHORT_DESCRIPTION:
            await bot.set_my_short_description(short_description=SHORT_DESCRIPTION)
        if (await bot.get_my_description()).description != DESCRIPTION:
            await bot.set_my_description(description=DESCRIPTION)
    except TelegramBadRequest as e:
        log.warning("Не удалось обновить описание бота: %s", e)


async def reset_menu(bot: Bot) -> None:
    """При остановке убираем кнопку мини-приложения: адрес туннеля больше не работает."""
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception:  # noqa: BLE001
        pass


def _webapp_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="🗺 Открыть путеводитель с картой", web_app=WebAppInfo(url=PUBLIC_URL))]


def _allowed(message_or_query: Message | CallbackQuery) -> bool:
    user = message_or_query.from_user
    return bool(user) and settings.is_allowed(user.id)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    if not _allowed(message):
        await message.answer("Этот путеводитель приватный.")
        return
    text = ("👋 Привет! Я <b>голосовой гид по Карелии</b>.\n\n"
            "🎙 Отправьте <b>голосовое сообщение</b> с вопросом — отвечу текстом и голосом. "
            "Например: «Как добраться до Кижей?» или «Что посмотреть в Рускеале?»\n\n"
            "⌨️ Можно и текстом. /places — список мест.")
    if PUBLIC_URL:
        text += "\n\n🗺 В путеводителе — карта Карелии и кнопка микрофона."
        markup = InlineKeyboardMarkup(inline_keyboard=[_webapp_row()])
    else:
        markup = None
    await message.answer(text, reply_markup=markup)


@router.message(Command("help"))
async def on_help(message: Message) -> None:
    await message.answer(
        "<b>Как пользоваться</b>\n"
        "• Запишите голосовое с вопросом о Карелии — я распознаю его и отвечу.\n"
        "• Можно спрашивать дальше: «а сколько стоит?», «как туда добраться?».\n"
        "• /places — главные места, у каждого есть точка на карте.\n"
        "• Кнопка «Путеводитель» внизу открывает мини-приложение с картой.\n\n"
        f"Режим ответов: {'Claude' if assistant.mode == 'claude' else 'база знаний гида'}."
    )


@router.message(Command("places"))
async def on_places(message: Message) -> None:
    if not _allowed(message):
        return
    buttons = [InlineKeyboardButton(text=f"{p.emoji} {p.name}", callback_data=f"place:{p.id}") for p in kb.places]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    if PUBLIC_URL:
        rows.append(_webapp_row())
    await message.answer("Выберите место — расскажу о нём:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("place:"))
async def on_place(query: CallbackQuery) -> None:
    place_id = query.data.split(":", 1)[1]
    if not _allowed(query) or place_id not in kb.by_id:
        await query.answer()
        return
    await query.answer()
    answer = assistant.about_place(f"tg:{query.from_user.id}", place_id)
    await _send_answer(query.bot, query.from_user.id, answer)


@router.callback_query(F.data.startswith("map:"))
async def on_map(query: CallbackQuery) -> None:
    place = kb.by_id.get(query.data.split(":", 1)[1])
    await query.answer()
    if place and _allowed(query):
        await query.bot.send_venue(chat_id=query.from_user.id, latitude=place.lat, longitude=place.lon,
                                   title=place.title, address=place.location)


@router.message(F.voice | F.audio | F.video_note)
async def on_voice(message: Message) -> None:
    if not _allowed(message):
        return
    media = message.voice or message.audio or message.video_note
    if media.duration and media.duration > 60:
        await message.reply("Запись длиннее минуты — задайте, пожалуйста, вопрос покороче.")
        return
    if not stt.ready:
        await message.reply("⏳ Загружаю модель распознавания речи — первый раз это может занять пару минут…")
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        audio = await message.bot.download(media)
        try:
            question = await stt.transcribe(audio.read())
        except Exception as e:  # noqa: BLE001
            log.exception("Ошибка распознавания")
            await message.reply(f"Не получилось распознать запись: {html.escape(str(e))}")
            return
        if not question:
            await message.reply("Не расслышал вопрос 🙉 Попробуйте ещё раз, чуть громче и ближе к микрофону.")
            return
        answer = await assistant.ask(f"tg:{message.from_user.id}", question)
    await _send_answer(message.bot, message.chat.id, answer, question=question)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message) -> None:
    if not _allowed(message):
        return
    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        answer = await assistant.ask(f"tg:{message.from_user.id}", message.text)
    await _send_answer(message.bot, message.chat.id, answer)


async def _send_answer(bot: Bot, chat_id: int, answer: Answer, question: str | None = None) -> None:
    parts = []
    if question:
        parts.append(f"🎙 <i>«{html.escape(question)}»</i>\n")
    parts.append(html.escape(answer.text))
    if answer.sources:
        links = ", ".join(f'<a href="{html.escape(s["url"])}">{html.escape(s["label"])}</a>'
                          for s in answer.sources[:3])
        parts.append(f"\n<i>Источники: {links}</i>")
    rows = []
    if answer.place:
        rows.append([InlineKeyboardButton(text=f"📍 {answer.place.name} на карте",
                                          callback_data=f"map:{answer.place.id}")])
    if PUBLIC_URL:
        rows.append(_webapp_row())
    await bot.send_message(chat_id, "\n".join(parts),
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
    if answer.recording:
        try:
            await bot.send_voice(chat_id, BufferedInputFile(await tts.recording_voice(answer.recording), "opisanie.ogg"))
        except Exception:  # noqa: BLE001 — например, голосовые запрещены: шлём исходный файл
            log.info("Голосовое с записью не отправилось, шлю аудиофайлом")
            await bot.send_audio(chat_id, BufferedInputFile(answer.recording.read_bytes(), answer.recording.name),
                                 title=answer.place.title if answer.place else "Описание",
                                 performer="Карельский гид")
        return
    try:
        async with ChatActionSender.record_voice(bot=bot, chat_id=chat_id):
            audio, fmt = await tts.synthesize_voice(answer.speech)
        if fmt == "ogg":
            try:
                await bot.send_voice(chat_id, BufferedInputFile(audio, "otvet.ogg"))
                return
            except TelegramBadRequest as e:  # у пользователя запрещены голосовые сообщения
                log.info("Голосовое не отправилось (%s), шлю аудиофайлом", e)
                audio = await tts.synthesize_mp3(answer.speech)
        await bot.send_audio(chat_id, BufferedInputFile(audio, "otvet.mp3"), title="Ответ гида",
                             performer="Карельский гид")
    except Exception:  # noqa: BLE001 — текст уже отправлен
        log.exception("Не удалось отправить озвучку")
