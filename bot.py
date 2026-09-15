import asyncio
import html
import io
import logging
import os
import re
import ssl
import tempfile
import time
from datetime import datetime

import aiohttp
import certifi
from dotenv import load_dotenv

from typing import Callable, Dict, Any, Awaitable
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramAPIError, TelegramEntityTooLarge
from aiogram.types import (
    BufferedInputFile,
    FSInputFile,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BotCommand,
    BotCommandScopeDefault,
)
import base64
import hashlib

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, COMM, TLEN, APIC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

import database

load_dotenv()

# ─── Конфигурация ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в файле .env!")

CHANNEL_URL          = os.getenv("CHANNEL_URL", "https://t.me/youtubestantg")
REQUIRED_CHANNEL     = os.getenv("REQUIRED_CHANNEL", "@youtubestantg").strip()
if REQUIRED_CHANNEL.startswith("https://t.me/"):
    REQUIRED_CHANNEL = "@" + REQUIRED_CHANNEL.split("https://t.me/")[1].strip("/")
SUPPORT_USERNAME     = os.getenv("SUPPORT_USERNAME", "youtubestanmanager")
SUPPORT_URL          = f"https://t.me/{SUPPORT_USERNAME}"
MAX_CONCURRENT       = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", 5))
RATE_LIMIT_SECONDS   = int(os.getenv("RATE_LIMIT_SECONDS", 10))
MAX_LINKS_PER_MSG    = int(os.getenv("MAX_LINKS_PER_MESSAGE", 5))
ADMIN_ID             = int(os.getenv("ADMIN_ID", 0))   # 0 = не задан
CACHE_TTL_DAYS       = int(os.getenv("CACHE_TTL_DAYS", 30))
MAX_MIX_TRACKS       = int(os.getenv("MAX_MIX_TRACKS", 10))
FREE_DAILY_DOWNLOADS = int(os.getenv("FREE_DAILY_DOWNLOADS", 20))
FREE_DAILY_MIXES     = int(os.getenv("FREE_DAILY_MIXES", 5))
FREE_DAILY_WAV       = int(os.getenv("FREE_DAILY_WAV", 1))
FREE_MAX_MIX_TRACKS  = int(os.getenv("FREE_MAX_MIX_TRACKS", 5))
REFERRALS_FOR_PRO    = int(os.getenv("REFERRALS_FOR_PRO", 3))

# ─── Логирование ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("SunoBot")

# ─── SSL ───────────────────────────────────────────────────────────────────────
ssl_ctx = ssl.create_default_context(cafile=certifi.where())

# ─── Исключения ───────────────────────────────────────────────────────────────
class TrackNotFoundError(Exception):
    """Вызывается, если трек удалён, приватный или не существует на Suno (404/403)."""
    pass


class TrackStillProcessingError(Exception):
    """Вызывается, если трек только что создан и ещё генерируется/обрабатывается серверами Suno."""
    pass


class WavTooLargeError(Exception):
    """Вызывается, если WAV файл даже на минимальной частоте дискретизации превышает лимит Telegram (50 МБ)."""
    pass


# ─── Алерты об ошибках администратору ──────────────────────────────────────────
_admin_error_timestamps: dict[str, float] = {}


async def notify_admin_error(
    context: str,
    error: Exception | str,
    extra_info: str = "",
    user: types.User | int | None = None,
):
    """Отправляет уведомление об ошибке администратору с информацией о пользователе и защитой от спама."""
    if not ADMIN_ID:
        return

    if isinstance(error, (TrackNotFoundError, TrackStillProcessingError, WavTooLargeError)):
        return

    err_type = type(error).__name__ if isinstance(error, Exception) else "Error"
    u_id = user.id if isinstance(user, types.User) else (user if isinstance(user, int) else 0)
    key = f"{context}:{err_type}:{u_id}"
    now = time.time()
    last_sent = _admin_error_timestamps.get(key, 0)
    if now - last_sent < 60:  # не чаще 1 раза в минуту для одного пользователя и ошибки
        return
    _admin_error_timestamps[key] = now

    err_text = str(error)
    if len(err_text) > 400:
        err_text = err_text[:400] + "..."

    # Формируем блок пользователя
    user_str = "<i>Системный процесс</i>"
    if isinstance(user, types.User):
        name_parts = [user.first_name or "", user.last_name or ""]
        full_name = html.escape(" ".join(p for p in name_parts if p).strip() or "Пользователь")
        if user.username:
            user_str = f"@{user.username} (ID: <code>{user.id}</code>, {full_name})"
        else:
            user_str = f'<a href="tg://user?id={user.id}">{full_name}</a> (ID: <code>{user.id}</code>, без @username)'
    elif isinstance(user, int) and user > 0:
        user_str = f'<a href="tg://user?id={user}">ID {user}</a>'

    msg = (
        f"🚨 <b>Алерт SunoSaver</b>\n\n"
        f"👤 <b>Пользователь:</b> {user_str}\n"
        f"📍 <b>Контекст:</b> {html.escape(context)}\n"
        f"⚠️ <b>Тип:</b> <code>{html.escape(err_type)}</code>\n"
        f"📝 <b>Ошибка:</b> <code>{html.escape(err_text)}</code>"
    )
    if extra_info:
        msg += f"\nℹ️ <b>Детали:</b> {html.escape(extra_info)}"

    msg += f"\n\n⏱ <i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</i>"

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=msg,
            parse_mode="HTML",
            disable_notification=False,
        )
    except Exception as ex:
        logger.warning("Не удалось отправить алерт админу: %s", ex)

# ─── Bot & Dispatcher ──────────────────────────────────────────────────────────
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

# ─── Глобальные объекты (создаются внутри main() после старта event loop) ──────
SEMAPHORE:    asyncio.Semaphore    | None = None
HTTP_SESSION: aiohttp.ClientSession | None = None

# ─── Rate limiting ─────────────────────────────────────────────────────────────
_user_last_request: dict[int, float] = {}

# ─── Паттерны ──────────────────────────────────────────────────────────────────
UUID_PATTERN    = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
SHORT_ID_PATTERN = re.compile(r"suno\.com/s/([A-Za-z0-9_-]+)")
PLAYLIST_PATTERN = re.compile(r"suno\.com/playlist/([0-9a-fA-F-]+)")
URL_FINDER       = re.compile(r"https?://[^\s<>\"'()]+")
SUNO_DOMAINS     = {"suno.com", "share.suno.ai"}

# ─── HTTP-заголовки ────────────────────────────────────────────────────────────
POST_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Content-Type":    "application/json",
    "Origin":          "https://sunodownload.io",
    "Referer":         "https://sunodownload.io/ru/",
    "Accept":          "*/*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}
GET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Referer":    "https://sunodownload.io/ru/",
    "Accept":     "*/*",
}

# ─── Тексты ────────────────────────────────────────────────────────────────────
TEXTS = {
    "ru": {
        "start": (
            "👋 <b>Привет!</b> Я помогу быстро скачать трек с <b>Suno AI</b> в MP3.\n\n"
            "🔗 <b>Отправьте ссылку на песню:</b>\n"
            "<code>https://suno.com/song/...</code>\n"
            "<code>https://suno.com/s/...</code>\n"
            "<code>https://share.suno.ai/...</code>\n\n"
            "💡 Можно сразу несколько ссылок в одном сообщении!\n\n"
            "Или выберите раздел в меню внизу 👇"
        ),
        "help": (
            "📖 <b>Как пользоваться ботом Suno Saver:</b>\n\n"
            "1️⃣ <b>Скачать песню:</b>\n"
            "Откройте трек на suno.com → <b>«Поделиться»</b> (Share) → скопируйте ссылку и отправьте в этот чат. Бот пришлёт готовый MP3 с тегами и обложкой!\n"
            "<i>Поддерживаются ссылки вида:</i> <code>suno.com/song/...</code>, <code>suno.com/s/...</code>\n\n"
            "2️⃣ <b>Текст песни и WAV:</b>\n"
            "Под каждым отправленным MP3 нажмите <b>«📜 Текст песни»</b> для просмотра слов или <b>«🎼 Скачать WAV»</b> для файла студийного качества.\n\n"
            "3️⃣ <b>Создать микс из треков:</b>\n"
            "Нажмите <b>«🎛 Создать микс»</b> (или команда /mix). Выберите до 10 песен и склейте их в один непрерывный аудиофайл (встык или плавный DJ-микс с кроссфейдом)!\n\n"
            "💬 <b>Служба поддержки:</b> @youtubestanmanager\n\n"
            "📦 <i>Можно отправлять до 5 ссылок в одном сообщении.</i>"
        ),
        "about": (
            "ℹ️ <b>О сервисе Suno Saver (@sunosaver_bot):</b>\n\n"
            "Универсальный и быстрый помощник для работы с музыкой из <b>Suno AI</b>.\n\n"
            "✨ <b>Главные возможности:</b>\n"
            "• 🎵 <b>MP3 и WAV</b> — мгновенное скачивание треков в высоком качестве.\n"
            "• 📜 <b>Текст песни</b> — просмотр официального текста трека (Lyrics).\n"
            "• 🎛 <b>Конструктор миксов</b> — объединение до 10 песен в один цельный сет (обычная склейка или плавный DJ Crossfade с таймкодами).\n"
            "• 🏷 <b>ID3-теги и обложки</b> — название, автор и арт вшиты прямо в аудиофайл.\n"
            "• ⚡️ <b>Умный кэш</b> — мгновенная отдача ранее скачанных треков.\n"
            "• 📦 <b>Пакетная загрузка</b> — до 5 ссылок одновременно в сообщении.\n"
            "• 🌍 <b>Мультиязычность</b> — Русский, English, Қазақша.\n\n"
            f"📢 <b>Наш официальный канал:</b> {CHANNEL_URL}\n"
            f"💬 <b>Поддержка:</b> @youtubestanmanager"
        ),
        "settings":      "⚙️ <b>Настройки интерфейса</b>\n\nВыберите язык:",
        "lang_changed":  "✅ Язык переключен на <b>Русский</b>!",
        "channel_msg":   f"📢 <b>Наш официальный канал:</b>\nПодписывайтесь: {CHANNEL_URL}",
        "fetching":      "⏳ Обрабатываю ссылку...",
        "downloading":   "📥 Загружаю в Telegram...",
        "cdn_fallback":  "🔄 Основной сервис недоступен, пробую резервный CDN...",
        "error_download":"❌ Не удалось скачать трек. Убедитесь, что он публичный.",
        "error_track_not_found": "❌ Трек не найден на Suno.\nВозможно, он был удалён автором или является приватным.",
        "error_track_processing": "⏳ Трек ещё генерируется или обрабатывается серверами Suno.\nПожалуйста, подождите 20–30 секунд и отправьте ссылку снова!",
        "error_telegram":"❌ Ошибка при отправке файла. Попробуйте позже.",
        "error_contact": "\n\n💬 Если возникла ошибка или есть вопрос, напишите: @youtubestanmanager",
        "error_rate_limit": "⏳ Не так быстро! Подождите немного.",
        "banned":        "🚫 <b>Вы заблокированы</b> и не можете использовать бота.",
        "multiple_links":"🔗 Нашёл <b>{count}</b> ссылок. Скачиваю по очереди...",
        "artist_label":  "👤 Автор",
        "downloaded_via": "⚡️ <b>Скачано через:</b> @sunosaver_bot",
        "channel_sub_link": "📢 <b>Канал:</b>",
        "btn_lyrics":     "📜 Текст песни",
        "btn_wav":        "🎼 Скачать WAV",
        "btn_video":      "🎬 Скачать видео",
        "lyrics_title":   "📜 <b>Текст песни «{title}»:</b>\n\n{lyrics}",
        "lyrics_none":    "ℹ️ У этого трека нет текста (инструментал).",
        "wav_generating": "⏳ Конвертирую и загружаю WAV (30-50 МБ)...",
        "wav_error":      "❌ Не удалось подготовить WAV файл. Попробуйте позже.",
        "wav_too_large":  "⚠️ Этот трек слишком длинный для формата WAV (лимит Telegram — 50 МБ).\nРекомендуем слушать или скачивать трек в формате MP3.",
        "video_generating":"⏳ Создаю и загружаю видеоклип (MP4)...",
        "video_error":    "❌ Не удалось подготовить видео. Попробуйте позже.",
        "btn_download_own": "🤖 Скачать свой трек",
        "btn_how_to":    "📥 Как скачать?",
        "btn_settings":  "⚙️ Настройки",
        "btn_about":     "ℹ️ О боте",
        "btn_channel":   "📢 Наш канал",
        "btn_go_channel":"➡️ Перейти в канал",
        "sub_required": (
            "📢 <b>Подпишитесь на наш канал, чтобы пользоваться ботом!</b>\n\n"
            "Подписка бесплатная и открывает полный доступ к скачиванию песен, студийному WAV, видео и созданию миксов.\n\n"
            "Подпишитесь на канал ниже и нажмите <b>«✅ Я подписался»</b>:"
        ),
        "btn_sub_channel": "📢 Подписаться на канал",
        "btn_check_sub":   "✅ Я подписался",
        "sub_success":     "✅ Спасибо за подписку! Теперь все функции бота вам доступны.",
        "sub_failed":      "❌ Вы ещё не подписались на канал! Пожалуйста, перейдите в канал, нажмите «Подписаться» и попробуйте снова.",
        "btn_create_mix": "🎛 Создать микс",
        "mix_menu_title": (
            "🎛 <b>Конструктор миксов Suno</b>\n\n"
            "Объедините несколько песен в один цельный MP3-трек!\n\n"
            "1️⃣ Выберите треки из вашей библиотеки ниже <i>(или отправьте ссылки на треки сообщением)</i>.\n"
            "2️⃣ Нажмите <b>«Собрать микс»</b> и выберите тип склейки (обычная или плавный DJ-микс)."
        ),
        "mix_empty_library": "ℹ️ В вашей библиотеке пока нет сохранённых треков. Отправьте ссылку на трек Suno прямо сейчас, чтобы добавить его в микс:",
        "mix_current_queue": "\n\n<b>Выбрано для микса ({count}/{max_tracks}):</b>\n{list}",
        "mix_btn_build": "🚀 Собрать микс ({count})",
        "mix_btn_clear": "🗑 Очистить",
        "mix_btn_cancel": "❌ Закрыть",
        "mix_mode_prompt": "🎵 <b>Выберите тип сведения для микса:</b>",
        "mix_mode_normal": "▶️ Обычная склейка (встык)",
        "mix_mode_crossfade": "🎧 Плавный DJ-микс (Crossfade)",
        "mix_btn_back": "⬅️ Назад",
        "mix_processing": "⏳ Склеиваю <b>{count}</b> треков в единый микс...",
        "mix_error": "❌ Не удалось создать микс. Попробуйте снова.",
        "mix_too_large": "❌ Микс получился слишком большим для Telegram (лимит 50 МБ). Попробуйте уменьшить количество треков.",
        "mix_min_tracks": "⚠️ Выберите хотя бы 2 трека для создания микса!",
        "mix_max_reached": f"⚠️ В микс можно добавить не более {MAX_MIX_TRACKS} треков.",
        "mix_cancelled": "❌ Создание микса закрыто.",
        "mix_track_added": "✅ Трек «{title}» добавлен в микс ({count}/{max_tracks})!",
        "btn_mix_these": "🎛 Склеить эти треки в микс",
        "btn_pro": "⭐️ PRO / Рефералка",
        "btn_share_ref": "📤 Поделиться с другом",
        "ref_share_text": "Скачивай треки с Suno AI в высоком качестве, создавай DJ-миксы и качай студийный WAV через бота: {ref_url}",
        "pro_active_text": (
            "⭐️ <b>Ваш статус: PRO НАВСЕГДА</b> 🚀\n\n"
            "Вам доступны все премиальные возможности без ограничений:\n"
            "• ♾ <b>Безлимитное скачивание MP3</b>\n"
            "• 🎛 <b>Миксы до 10 песен разом + DJ Crossfade</b>\n"
            "• 🎼 <b>Безлимитный студийный WAV</b>\n"
            "• 🎬 <b>Приоритетный рендер видео MP4</b>\n\n"
            "👥 Вы пригласили друзей: <b>{invited}</b>\n\n"
            "🔗 <b>Ваша персональная ссылка:</b>\n<code>{ref_url}</code>"
        ),
        "pro_promo_text": (
            "⭐️ <b>Получите статус PRO НАВСЕГДА!</b> 🎁\n\n"
            "Пригласите всего <b>{total} друзей</b> в бота и снимите все ограничения навсегда!\n\n"
            "👥 <b>Ваш прогресс:</b> {invited} из {total} друзей <i>(осталось: {needed})</i>\n\n"
            "📊 <b>Ваши лимиты на сегодня:</b>\n"
            "• 🎵 Скачивание: <b>{dl_today} / {dl_max}</b> треков\n"
            "• 🎛 Миксы: <b>{mix_today} / {mix_max}</b> (до 5 песен)\n"
            "• 🎼 Студийный WAV: <b>{wav_today} / {wav_max}</b>\n\n"
            "⭐️ <b>Что даёт статус PRO:</b>\n"
            "✅ Полный безлимит на скачивание MP3\n"
            "✅ Миксы до 10 песен в один сет\n"
            "✅ Безлимитный студийный WAV (30-50 МБ)\n"
            "✅ Приоритетная скорость обработки\n\n"
            "🔗 <b>Ваша ссылка для приглашения:</b>\n<code>{ref_url}</code>"
        ),
        "limit_downloads_reached": (
            "⚠️ <b>Дневной лимит исчерпан!</b>\n\n"
            "Вы скачали <b>{limit} из {limit}</b> треков на сегодня. Лимит обновится в 00:00 UTC.\n\n"
            "⭐️ <b>Хотите полный безлимит навсегда?</b>\n"
            "Пригласите всего 3 друзей в бота по вашей ссылке:\n<code>{ref_url}</code>"
        ),
        "limit_mixes_reached": (
            "⚠️ <b>Дневной лимит миксов исчерпан!</b>\n\n"
            "Вы создали <b>{limit} из {limit}</b> миксов на сегодня.\n\n"
            "⭐️ Пригласите 3 друзей и создавайте <b>миксы без ограничений</b>:\n<code>{ref_url}</code>"
        ),
        "limit_mixes_alert": "⚠️ Дневной лимит миксов исчерпан!",
        "limit_mix_tracks_free": (
            "⚠️ В бесплатной версии можно склеить до {free_max} песен в один микс.\n\n"
            "⭐️ В <b>PRO-аккаунте</b> доступно объединение до 10 песен!\n"
            "Пригласите 3 друзей, чтобы разблокировать PRO навсегда:\n<code>{ref_url}</code>"
        ),
        "limit_mix_tracks_alert": "⚠️ В бесплатной версии микс до 5 песен! Откройте PRO для 10 песен.",
        "limit_wav_reached": (
            "⚠️ <b>Дневной лимит WAV исчерпан!</b>\n\n"
            "В бесплатной версии доступен 1 студийный WAV в день.\n\n"
            "⭐️ Пригласите 3 друзей и качайте <b>WAV без ограничений</b>:\n<code>{ref_url}</code>"
        ),
        "limit_wav_alert": "⚠️ Дневной лимит WAV исчерпан! Откройте PRO за 3 друзей.",
        "ref_progress": (
            "🎉 <b>По вашей ссылке зарегистрировался друг!</b>\n\n"
            "👥 Приглашено: <b>{invited}/{total}</b>\n"
            "Осталось пригласить ещё <b>{remaining}</b> до статуса <b>PRO НАВСЕГДА</b>! ⭐️"
        ),
        "ref_pro_unlocked": (
            "🔥 <b>ПОЗДРАВЛЯЕМ! ВЫ ПОЛУЧИЛИ PRO НАВСЕГДА!</b> ⭐️\n\n"
            "Вы успешно пригласили <b>{total} друзей</b>!\n"
            "Все дневные лимиты сняты, активирован безлимитный WAV и миксы до 10 песен. Спасибо, что вы с нами! 🚀"
        ),
        "artist_auto": "Авто (автор из Suno)",
        "artist_info": (
            "👤 <b>Настройка авторства треков</b>\n\n"
            "Текущий автор: <b>{current}</b>\n\n"
            "Чтобы установить своё имя или ник во всех скачиваемых песнях, отправьте:\n"
            "<code>/artist МойНик</code>\n\n"
            "Чтобы вернуть автоопределение автора из Suno, отправьте:\n"
            "<code>/artist reset</code>"
        ),
        "artist_set_done": "✅ Теперь в поле «Исполнитель» во всех ваших треках будет указываться: <b>{artist}</b>\n<i>(Чтобы сбросить, отправьте /artist reset)</i>",
        "artist_reset_done": "✅ Настройки сброшены! Теперь автор будет определяться автоматически из Suno.",
        "artist_invalid": "❌ Недопустимое имя автора. Пожалуйста, укажите корректный текст (до 40 символов).",
        "pl_title": "Плейлист",
        "pl_total_tracks": "Всего треков",
        "pl_tracks_word": "треков",
        "pl_prompt_action": "Выберите действие:",
        "btn_pl_download": "📥 Скачать все треки ({count})",
        "btn_pl_mix": "🎛 Собрать плейлист в микс",
        "pl_not_found": "❌ Не удалось загрузить плейлист. Убедитесь, что ссылка верна и плейлист публичный.",
        "pl_downloading": "📥 Начинаю скачивание плейлиста ({count} треков)...",
        "pl_mix_added": "✅ <b>{count}</b> треков из плейлиста добавлены в конструктор микса!",
    },
    "en": {
        "start": (
            "👋 <b>Hello!</b> I'll help you download any <b>Suno AI</b> song as MP3.\n\n"
            "🔗 <b>Send me a link:</b>\n"
            "<code>https://suno.com/song/...</code>\n"
            "<code>https://suno.com/s/...</code>\n"
            "<code>https://share.suno.ai/...</code>\n\n"
            "💡 You can send multiple links in one message!\n\n"
            "Or use the buttons below 👇"
        ),
        "help": (
            "📖 <b>How to use Suno Saver bot:</b>\n\n"
            "1️⃣ <b>Download a song:</b>\n"
            "Open your track on suno.com → click <b>«Share»</b> → copy link and send it here. The bot sends back high quality MP3 with tags and artwork!\n"
            "<i>Supported link formats:</i> <code>suno.com/song/...</code>, <code>suno.com/s/...</code>\n\n"
            "2️⃣ <b>Lyrics & WAV:</b>\n"
            "Under each sent MP3, click <b>«📜 Lyrics»</b> to view song words or <b>«🎼 Download WAV»</b> for lossless studio audio.\n\n"
            "3️⃣ <b>Create a Mix:</b>\n"
            "Click <b>«🎛 Create Mix»</b> (or send /mix). Pick up to 10 tracks and stitch them into a single continuous file (Gapless or Smooth DJ Crossfade with timestamps)!\n\n"
            "💬 <b>Support:</b> @youtubestanmanager\n\n"
            "📦 <i>Up to 5 links in a single message.</i>"
        ),
        "about": (
            "ℹ️ <b>About Suno Saver (@sunosaver_bot):</b>\n\n"
            "Your fast and powerful companion for downloading and managing music from <b>Suno AI</b>.\n\n"
            "✨ <b>Main Features:</b>\n"
            "• 🎵 <b>MP3 & Studio WAV</b> — instant high-quality audio downloads.\n"
            "• 📜 <b>Song Lyrics</b> — extract official song lyrics and structure.\n"
            "• 🎛 <b>Suno Mix Maker</b> — combine up to 10 tracks into a single seamless set (Gapless or smooth DJ Crossfade with timestamps).\n"
            "• 🏷 <b>ID3 Tags & Artwork</b> — title, artist, and album artwork embedded into every file.\n"
            "• ⚡️ <b>Smart Cache</b> — instant redelivery of previously requested tracks.\n"
            "• 📦 <b>Batch Downloads</b> — up to 5 links in one message.\n"
            "• 🌍 <b>Multilingual</b> — Russian, English, Kazakh.\n\n"
            f"📢 <b>Official Channel:</b> {CHANNEL_URL}\n"
            f"💬 <b>Support:</b> @youtubestanmanager"
        ),
        "settings":      "⚙️ <b>Settings</b>\n\nChoose language:",
        "lang_changed":  "✅ Language changed to <b>English</b>!",
        "channel_msg":   f"📢 <b>Our Official Channel:</b>\nSubscribe: {CHANNEL_URL}",
        "fetching":      "⏳ Processing link...",
        "downloading":   "📥 Uploading to Telegram...",
        "cdn_fallback":  "🔄 Main service unavailable, trying fallback CDN...",
        "error_download":"❌ Could not download the track. Make sure it's public.",
        "error_track_not_found": "❌ Track not found on Suno.\nIt may have been deleted by the author or set to private.",
        "error_track_processing": "⏳ The track is still being generated or processed by Suno servers.\nPlease wait 20–30 seconds and send the link again!",
        "error_telegram":"❌ Error delivering file. Please try again later.",
        "error_contact": "\n\n💬 If an error occurred or you need help: @youtubestanmanager",
        "error_rate_limit": "⏳ Slow down! Please wait a moment.",
        "banned":        "🚫 <b>You are banned</b> and cannot use this bot.",
        "multiple_links":"🔗 Found <b>{count}</b> links. Downloading one by one...",
        "artist_label":  "👤 Artist",
        "downloaded_via": "⚡️ <b>Downloaded via:</b> @sunosaver_bot",
        "channel_sub_link": "📢 <b>Channel:</b>",
        "btn_lyrics":     "📜 Lyrics",
        "btn_wav":        "🎼 Download WAV",
        "btn_video":      "🎬 Download Video",
        "lyrics_title":   "📜 <b>Lyrics for «{title}»:</b>\n\n{lyrics}",
        "lyrics_none":    "ℹ️ This track has no lyrics (instrumental).",
        "wav_generating": "⏳ Converting and uploading WAV (30-50 MB)...",
        "wav_error":      "❌ Could not prepare WAV file. Please try again later.",
        "wav_too_large":  "⚠️ This track is too long for uncompressed WAV format (Telegram limit is 50 MB).\nPlease download the track in MP3 format.",
        "video_generating":"⏳ Generating and uploading video (MP4)...",
        "video_error":    "❌ Could not prepare video. Please try again later.",
        "btn_download_own": "🤖 Download Your Track",
        "btn_how_to":    "📥 How to download?",
        "btn_settings":  "⚙️ Settings",
        "btn_about":     "ℹ️ About",
        "btn_channel":   "📢 Our Channel",
        "btn_go_channel":"➡️ Go to Channel",
        "sub_required": (
            "📢 <b>Please subscribe to our channel to use the bot:</b>\n\n"
            "Subscription is free and unlocks full access to song downloads, studio WAV, video, and music mixes.\n\n"
            "Join the channel below and click <b>«✅ I have subscribed»</b>:"
        ),
        "btn_sub_channel": "📢 Subscribe to Channel",
        "btn_check_sub":   "✅ I have subscribed",
        "sub_success":     "✅ Thank you for subscribing! All bot features are now available.",
        "sub_failed":      "❌ You haven't subscribed to the channel yet! Please join the channel and try again.",
        "btn_create_mix": "🎛 Create Mix",
        "mix_menu_title": (
            "🎛 <b>Suno Mix Maker</b>\n\n"
            "Combine multiple songs into one seamless MP3 track!\n\n"
            "1️⃣ Choose tracks from your library below <i>(or send Suno links in chat)</i>.\n"
            "2️⃣ Click <b>«Build Mix»</b> and choose transition type (Normal or DJ Crossfade)."
        ),
        "mix_empty_library": "ℹ️ Your library is currently empty. Send a Suno link right now to add it to the mix:",
        "mix_current_queue": "\n\n<b>Selected for mix ({count}/{max_tracks}):</b>\n{list}",
        "mix_btn_build": "🚀 Build Mix ({count})",
        "mix_btn_clear": "🗑 Clear",
        "mix_btn_cancel": "❌ Close",
        "mix_mode_prompt": "🎵 <b>Choose transition mode for your mix:</b>",
        "mix_mode_normal": "▶️ Normal (Gapless)",
        "mix_mode_crossfade": "🎧 Smooth DJ Crossfade",
        "mix_btn_back": "⬅️ Back",
        "mix_processing": "⏳ Stitching <b>{count}</b> tracks into a single mix...",
        "mix_error": "❌ Could not create mix. Please try again.",
        "mix_too_large": "❌ The mix exceeds Telegram's 50 MB limit. Try choosing fewer tracks.",
        "mix_min_tracks": "⚠️ You need at least 2 tracks to create a mix!",
        "mix_max_reached": f"⚠️ You can add up to {MAX_MIX_TRACKS} tracks in a single mix.",
        "mix_cancelled": "❌ Mix builder closed.",
        "mix_track_added": "✅ Track «{title}» added to mix ({count}/{max_tracks})!",
        "btn_mix_these": "🎛 Stitch these into a Mix",
        "btn_pro": "⭐️ PRO / Referrals",
        "btn_share_ref": "📤 Share with a friend",
        "ref_share_text": "Download Suno AI tracks in high quality, build DJ mixes and get studio WAV audio via bot: {ref_url}",
        "pro_active_text": (
            "⭐️ <b>Your Status: PRO FOREVER</b> 🚀\n\n"
            "All premium perks are fully unlocked for you:\n"
            "• ♾ <b>Unlimited MP3 downloads</b>\n"
            "• 🎛 <b>Mixes up to 10 tracks + DJ Crossfade</b>\n"
            "• 🎼 <b>Unlimited studio lossless WAV</b>\n"
            "• 🎬 <b>Priority MP4 video render</b>\n\n"
            "👥 Invited friends: <b>{invited}</b>\n\n"
            "🔗 <b>Your referral link:</b>\n<code>{ref_url}</code>"
        ),
        "pro_promo_text": (
            "⭐️ <b>Get PRO Status FOREVER!</b> 🎁\n\n"
            "Invite only <b>{total} friends</b> to the bot and unlock all features forever!\n\n"
            "👥 <b>Your Progress:</b> {invited} of {total} friends <i>({needed} remaining)</i>\n\n"
            "📊 <b>Your daily limits:</b>\n"
            "• 🎵 Downloads: <b>{dl_today} / {dl_max}</b> tracks\n"
            "• 🎛 Mixes: <b>{mix_today} / {mix_max}</b> (up to 5 tracks)\n"
            "• 🎼 Studio WAV: <b>{wav_today} / {wav_max}</b>\n\n"
            "⭐️ <b>What PRO gives you:</b>\n"
            "✅ Unlimited MP3 downloads\n"
            "✅ Up to 10 tracks in a single mix\n"
            "✅ Unlimited studio lossless WAV (30-50 MB)\n"
            "✅ Priority queue\n\n"
            "🔗 <b>Your invite link:</b>\n<code>{ref_url}</code>"
        ),
        "limit_downloads_reached": (
            "⚠️ <b>Daily download limit reached!</b>\n\n"
            "You have downloaded <b>{limit} of {limit}</b> tracks today. Resets at 00:00 UTC.\n\n"
            "⭐️ <b>Want unlimited downloads forever?</b>\n"
            "Invite 3 friends to the bot with your link:\n<code>{ref_url}</code>"
        ),
        "limit_mixes_reached": (
            "⚠️ <b>Daily mix limit reached!</b>\n\n"
            "You have created <b>{limit} of {limit}</b> mixes today.\n\n"
            "⭐️ Invite 3 friends and get <b>unlimited mixes forever</b>:\n<code>{ref_url}</code>"
        ),
        "limit_mixes_alert": "⚠️ Daily mix limit reached!",
        "limit_mix_tracks_free": (
            "⚠️ Free version allows up to {free_max} tracks per mix.\n\n"
            "⭐️ <b>PRO version</b> allows combining up to 10 tracks!\n"
            "Invite 3 friends to unlock PRO forever:\n<code>{ref_url}</code>"
        ),
        "limit_mix_tracks_alert": "⚠️ Free version limit is 5 tracks! Unlock PRO for 10 tracks.",
        "limit_wav_reached": (
            "⚠️ <b>Daily WAV limit reached!</b>\n\n"
            "Free version allows 1 studio WAV per day.\n\n"
            "⭐️ Invite 3 friends and download <b>unlimited WAV</b>:\n<code>{ref_url}</code>"
        ),
        "limit_wav_alert": "⚠️ Daily WAV limit reached! Unlock PRO by inviting 3 friends.",
        "ref_progress": (
            "🎉 <b>A friend joined via your link!</b>\n\n"
            "👥 Invited: <b>{invited}/{total}</b>\n"
            "Only <b>{remaining}</b> more to unlock <b>PRO FOREVER</b>! ⭐️"
        ),
        "ref_pro_unlocked": (
            "🔥 <b>CONGRATULATIONS! PRO UNLOCKED FOREVER!</b> ⭐️\n\n"
            "You have invited <b>{total} friends</b>!\n"
            "All daily limits are removed, unlimited studio WAV and 10-track mixes are now yours forever. Thank you! 🚀"
        ),
        "artist_auto": "Auto (Suno creator)",
        "artist_info": (
            "👤 <b>Custom Artist Settings</b>\n\n"
            "Current artist: <b>{current}</b>\n\n"
            "To set your own name/nickname for all downloaded tracks, send:\n"
            "<code>/artist YourNick</code>\n\n"
            "To reset back to auto-detecting creator from Suno, send:\n"
            "<code>/artist reset</code>"
        ),
        "artist_set_done": "✅ Artist name for all your downloaded tracks is now set to: <b>{artist}</b>\n<i>(To reset, send /artist reset)</i>",
        "artist_reset_done": "✅ Settings reset! Artist will now be automatically detected from Suno.",
        "artist_invalid": "❌ Invalid artist name. Please provide valid text (up to 40 characters).",
        "pl_title": "Playlist",
        "pl_total_tracks": "Total tracks",
        "pl_tracks_word": "tracks",
        "pl_prompt_action": "Choose an action:",
        "btn_pl_download": "📥 Download all tracks ({count})",
        "btn_pl_mix": "🎛 Build mix from playlist",
        "pl_not_found": "❌ Could not load playlist. Make sure the link is valid and public.",
        "pl_downloading": "📥 Starting playlist download ({count} tracks)...",
        "pl_mix_added": "✅ <b>{count}</b> tracks from the playlist added to mix builder!",
    },
    "kk": {
        "start": (
            "👋 <b>Сәлем!</b> Мен <b>Suno AI</b> тректерін MP3 форматында жылдам жүктеп алуға көмектесемін.\n\n"
            "🔗 <b>Әнге сілтеме жіберіңіз:</b>\n"
            "<code>https://suno.com/song/...</code>\n"
            "<code>https://suno.com/s/...</code>\n"
            "<code>https://share.suno.ai/...</code>\n\n"
            "💡 Бір хабарламада бірнеше сілтеме жіберуге болады!\n\n"
            "Немесе төмендегі мәзірді таңдаңыз 👇"
        ),
        "help": (
            "📖 <b>Suno Saver ботын қалай қолдану керек:</b>\n\n"
            "1️⃣ <b>Әнді жүктеу:</b>\n"
            "suno.com сайтында тректі ашыңыз → <b>«Бөлісу»</b> (Share) → сілтемені көшіріп чатқа жіберіңіз. Бот сапалы MP3 форматында мұқабасымен жібереді!\n"
            "<i>Қолдау көрсетілетін сілтемелер:</i> <code>suno.com/song/...</code>, <code>suno.com/s/...</code>\n\n"
            "2️⃣ <b>Ән мәтіні және WAV:</b>\n"
            "Жіберілген әр әннің астындағы <b>«📜 Ән мәтіні»</b> (сөздерін көру) немесе <b>«🎼 WAV жүктеу»</b> (студиялық таза дыбыс) батырмасын басыңыз.\n\n"
            "3️⃣ <b>Әндерден микс жасау:</b>\n"
            "Мәзірден <b>«🎛 Микс жасау»</b> (немесе /mix пәрмені) таңдаңыз. 10 әнге дейін таңдап, бір тұтас үзіліссіз аудиофайлға біріктіріңіз (кәдімгі немесе DJ Crossfade)!\n\n"
            "💬 <b>Қолдау қызметі:</b> @youtubestanmanager\n\n"
            "📦 <i>Бір хабарламада 5 сілтемеге дейін жіберуге болады.</i>"
        ),
        "about": (
            "ℹ️ <b>Suno Saver қызметі туралы (@sunosaver_bot):</b>\n\n"
            "<b>Suno AI</b> тректерімен жұмыс істеуге арналған жылдам әрі ыңғайлы көмекшіңіз.\n\n"
            "✨ <b>Негізгі мүмкіндіктері:</b>\n"
            "• 🎵 <b>MP3 және студиялық WAV</b> — тректерді жоғары сапада лезде жүктеу.\n"
            "• 📜 <b>Ән мәтіні (Lyrics)</b> — ресми сөздері мен құрылымын шығару.\n"
            "• 🎛 <b>Микс құрастырушысы</b> — 10 әнге дейін бір тұтас сетке біріктіру (кәдімгі немесе таймкодтары бар плавный DJ Crossfade).\n"
            "• 🏷 <b>ID3-тегтер мен мұқаба</b> — ән атауы, орындаушысы аудиофайлға ендірілген.\n"
            "• ⚡️ <b>Ақылды кэш</b> — бұрын жүктелген тректерді қас қағым сәтте қайта жіберу.\n"
            "• 📦 <b>Топтама жүктеу</b> — бір хабарламада бірден 5 сілтемеге дейін.\n"
            "• 🌍 <b>3 тілді толық қолдау</b> — Қазақша, Орысша, Ағылшынша.\n\n"
            f"📢 <b>Біздің ресми арна:</b> {CHANNEL_URL}\n"
            f"💬 <b>Қолдау қызметі:</b> @youtubestanmanager"
        ),
        "settings":      "⚙️ <b>Интерфейс баптаулары</b>\n\nТілді таңдаңыз:",
        "lang_changed":  "✅ Тіл <b>Қазақ тіліне</b> ауыстырылды!",
        "channel_msg":   f"📢 <b>Біздің ресми арна:</b>\nЖазылыңыз: {CHANNEL_URL}",
        "fetching":      "⏳ Сілтеме өңделуде...",
        "downloading":   "📥 Telegram-ға жүктелуде...",
        "cdn_fallback":  "🔄 Негізгі қызмет қолжетімсіз, қосалқы CDN тексерілуде...",
        "error_download":"❌ Тректі жүктеу мүмкін болмады. Оның ашық (public) екеніне көз жеткізіңіз.",
        "error_track_not_found": "❌ Трек Suno-дан табылмады.\nМүмкін, автор оны өшірген немесе жеке (private) жасаған.",
        "error_track_processing": "⏳ Трек әлі Suno серверлерінде өңделуде немесе жасалуда.\n20–30 секунд күтіп, сілтемені қайта жіберіңіз!",
        "error_telegram":"❌ Файлды жіберу кезінде қате орын алды. Кейінірек қайталап көріңіз.",
        "error_contact": "\n\n💬 Қате шықса немесе сұрағыңыз болса, жазыңыз: @youtubestanmanager",
        "error_rate_limit": "⏳ Тым жылдам! Біраз күте тұрыңыз.",
        "banned":        "🚫 <b>Сіз бұғатталғансыз</b> және ботты қолдана алмайсыз.",
        "multiple_links":"🔗 <b>{count}</b> сілтеме табылды. Кезекпен жүктелуде...",
        "artist_label":  "👤 Авторы",
        "downloaded_via": "⚡️ <b>Жүктелді:</b> @sunosaver_bot",
        "channel_sub_link": "📢 <b>Арна:</b>",
        "btn_lyrics":     "📜 Ән мәтіні",
        "btn_wav":        "🎼 WAV жүктеу",
        "btn_video":      "🎬 Видео жүктеу",
        "lyrics_title":   "📜 <b>«{title}» әнінің мәтіні:</b>\n\n<blockquote>{lyrics}</blockquote>",
        "lyrics_none":    "ℹ️ Бұл тректің сөзі жоқ (инструментал).",
        "wav_generating": "⏳ WAV пішіміне түрлендіру және жүктеу (30-50 МБ)...",
        "wav_error":      "❌ WAV файлын дайындау мүмкін болмады. Кейінірек көріңіз.",
        "wav_too_large":  "⚠️ Бұл трек қысылмаған WAV пішімі үшін тым ұзын (Telegram шегі — 50 МБ).\nТректі MP3 пішімінде жүктеп алуды ұсынамыз.",
        "video_generating":"⏳ Бейнеклип (MP4) дайындалуда және жүктелуде...",
        "video_error":    "❌ Бейнені дайындау мүмкін болмады. Кейінірек көріңіз.",
        "btn_download_own": "🤖 Өз трегіңізді жүктеу",
        "btn_how_to":    "📥 Қалай жүктейді?",
        "btn_settings":  "⚙️ Баптаулар",
        "btn_about":     "ℹ️ Бот туралы",
        "btn_channel":   "📢 Біздің арна",
        "btn_go_channel":"➡️ Арнаға өту",
        "sub_required": (
            "📢 <b>Ботты пайдалану үшін біздің арнаға жазылыңыз:</b>\n\n"
            "Жазылу тегін және әндерді, студиялық WAV, видео және микстерді шектеусіз жүктеуге мүмкіндік береді.\n\n"
            "Төмендегі арнаға жазылып, <b>«✅ Мен жазылдым»</b> түймесін басыңыз:"
        ),
        "btn_sub_channel": "📢 Арнаға жазылу",
        "btn_check_sub":   "✅ Мен жазылдым",
        "sub_success":     "✅ Жазылғаныңызға рахмет! Енді боттың барлық мүмкіндіктері ашық.",
        "sub_failed":      "❌ Сіз әлі арнаға жазылмадыңыз! Арнаға өтіп, «Жазылу» түймесін басып, қайта көріңіз.",
        "btn_create_mix": "🎛 Микс жасау",
        "mix_menu_title": (
            "🎛 <b>Suno Микс жасау шебері</b>\n\n"
            "Бірнеше әнді бір тұтас MP3 трекке біріктіріңіз!\n\n"
            "1️⃣ Төмендегі жеке кітапханаңыздан әндерді таңдаңыз <i>(немесе чатқа сілтеме жіберіңіз)</i>.\n"
            "2️⃣ <b>«Миксті құрастыру»</b> басып, біріктіру түрін таңдаңыз (кәдімгі немесе DJ кроссфейд)."
        ),
        "mix_empty_library": "ℹ️ Кітапханаңызда әзірге сақталған әндер жоқ. Микске қосу үшін қазір Suno сілтемесін жіберіңіз:",
        "mix_current_queue": "\n\n<b>Микс үшін таңдалды ({count}/{max_tracks}):</b>\n{list}",
        "mix_btn_build": "🚀 Миксті құрастыру ({count})",
        "mix_btn_clear": "🗑 Тазалау",
        "mix_btn_cancel": "❌ Жабу",
        "mix_mode_prompt": "🎵 <b>Миксті біріктіру түрін таңдаңыз:</b>",
        "mix_mode_normal": "▶️ Кәдімгі (тізбекті жалғау)",
        "mix_mode_crossfade": "🎧 Ырғақты DJ-микс (Crossfade)",
        "mix_btn_back": "⬅️ Артқа",
        "mix_processing": "⏳ <b>{count}</b> трек бір микске біріктірілуде...",
        "mix_error": "❌ Миксті жасау мүмкін болмады. Қайталап көріңіз.",
        "mix_too_large": "❌ Микс Telegram шегінен (50 МБ) асып кетті. Тректер санын азайтып көріңіз.",
        "mix_min_tracks": "⚠️ Микс жасау үшін кемінде 2 трек таңдау қажет!",
        "mix_max_reached": f"⚠️ Бір микске ең көбі {MAX_MIX_TRACKS} трек қосуға болады.",
        "mix_cancelled": "❌ Микс шебері жабылды.",
        "mix_track_added": "✅ «{title}» трегі микске қосылды ({count}/{max_tracks})!",
        "btn_mix_these": "🎛 Осы тректерден микс жасау",
        "btn_pro": "⭐️ PRO / Достар",
        "btn_share_ref": "📤 Доспен бөлісу",
        "ref_share_text": "Suno AI әндерін жоғары сапада жүкте, DJ-микстер жаса және студиялық WAV ал: {ref_url}",
        "pro_active_text": (
            "⭐️ <b>Сіздің мәртебеңіз: МӘҢГІЛІК PRO</b> 🚀\n\n"
            "Барлық премиум мүмкіндіктер шектеусіз ашық:\n"
            "• ♾ <b>Шектеусіз MP3 жүктеу</b>\n"
            "• 🎛 <b>10 әнге дейін микс жасау + DJ Crossfade</b>\n"
            "• 🎼 <b>Шектеусіз студиялық таза WAV</b>\n"
            "• 🎬 <b>Бейнеклиптерді басымдықпен рендерлеу</b>\n\n"
            "👥 Шақырған достарыңыз: <b>{invited}</b>\n\n"
            "🔗 <b>Жеке сілтемеңіз:</b>\n<code>{ref_url}</code>"
        ),
        "pro_promo_text": (
            "⭐️ <b>МӘҢГІЛІК PRO-СТАТУС АЛЫҢЫЗ!</b> 🎁\n\n"
            "Ботқа бар болғаны <b>{total} дос</b> шақырып, барлық шектеулерді мәңгіге алып тастаңыз!\n\n"
            "👥 <b>Сіздің нәтижеңіз:</b> {invited}/{total} дос <i>(тағы {needed} қажет)</i>\n\n"
            "📊 <b>Бүгінгі күндік лимиттеріңіз:</b>\n"
            "• 🎵 Жүктеу: <b>{dl_today} / {dl_max}</b> трек\n"
            "• 🎛 Микстер: <b>{mix_today} / {mix_max}</b> (5 әнге дейін)\n"
            "• 🎼 Студиялық WAV: <b>{wav_today} / {wav_max}</b>\n\n"
            "⭐️ <b>PRO не береді:</b>\n"
            "✅ MP3 жүктеуге толық шектеусіздік\n"
            "✅ Бір микске 10 әнге дейін қосу\n"
            "✅ Шектеусіз студиялық WAV (30-50 МБ)\n"
            "✅ Басымдықты жоғары жылдамдық\n\n"
            "🔗 <b>Достарды шақыру сілтемеңіз:</b>\n<code>{ref_url}</code>"
        ),
        "limit_downloads_reached": (
            "⚠️ <b>Күндік шектеу таусылды!</b>\n\n"
            "Бүгін <b>{limit} тректің {limit}-ін</b> жүктедіңіз. Лимит 00:00 UTC-де жаңарады.\n\n"
            "⭐️ <b>Шектеусіз мәңгілік PRO алғыңыз келе ме?</b>\n"
            "Сілтемеңіз арқылы 3 досыңызды шақырыңыз:\n<code>{ref_url}</code>"
        ),
        "limit_mixes_reached": (
            "⚠️ <b>Күндік микс шектеуі таусылды!</b>\n\n"
            "Бүгін <b>{limit} микстің {limit}-ін</b> жасадыңыз.\n\n"
            "⭐️ 3 дос шақырып, <b>шектеусіз микстер жасаңыз</b>:\n<code>{ref_url}</code>"
        ),
        "limit_mixes_alert": "⚠️ Күндік микс шектеуі таусылды!",
        "limit_mix_tracks_free": (
            "⚠️ Тегін нұсқада бір микске 5 әнге дейін біріктіруге болады.\n\n"
            "⭐️ <b>PRO-нұсқада</b> 10 әнге дейін рұқсат етілген!\n"
            "Мәңгілік PRO-ны ашу үшін 3 дос шақырыңыз:\n<code>{ref_url}</code>"
        ),
        "limit_mix_tracks_alert": "⚠️ Тегін нұсқада ең көбі 5 ән! 10 ән үшін PRO ашыңыз.",
        "limit_wav_reached": (
            "⚠️ <b>Күндік WAV шектеуі таусылды!</b>\n\n"
            "Тегін нұсқада күніне 1 студиялық WAV қолжетімді.\n\n"
            "⭐️ 3 дос шақырып, <b>WAV-ты шектеусіз жүктеңіз</b>:\n<code>{ref_url}</code>"
        ),
        "limit_wav_alert": "⚠️ Күндік WAV лимиті таусылды! 3 дос шақырып PRO алыңыз.",
        "ref_progress": (
            "🎉 <b>Сілтемеңіз арқылы жаңа дос қосылды!</b>\n\n"
            "👥 Шақырылды: <b>{invited}/{total}</b>\n"
            "<b>МӘҢГІЛІК PRO</b> алу үшін тағы <b>{remaining}</b> дос қалды! ⭐️"
        ),
        "ref_pro_unlocked": (
            "🔥 <b>ҚҰТТЫҚТАЙМЫЗ! СІЗГЕ МӘҢГІЛІК PRO БЕРІЛДІ!</b> ⭐️\n\n"
            "Сіз <b>{total} дос</b> шақырдыңыз!\n"
            "Барлық күндік шектеулер алынып тасталды, шектеусіз WAV және 10 әнге дейінгі микстер ашылды. Бізбен бірге болғаныңызға рақмет! 🚀"
        ),
        "artist_auto": "Авто (Suno авторы)",
        "artist_info": (
            "👤 <b>Трек авторын баптау</b>\n\n"
            "Қазіргі автор: <b>{current}</b>\n\n"
            "Барлық жүктелетін әндерге өз атыңызды немесе бүркеншік атыңызды орнату үшін жіберіңіз:\n"
            "<code>/artist МеніңНикім</code>\n\n"
            "Suno-дан автоанықтауға қайтару үшін жіберіңіз:\n"
            "<code>/artist reset</code>"
        ),
        "artist_set_done": "✅ Енді жүктелген барлық тректеріңіздің орындаушысы ретінде <b>{artist}</b> көрсетіледі\n<i>(Қайтару үшін /artist reset жіберіңіз)</i>",
        "artist_reset_done": "✅ Баптаулар қалпына келтірілді! Енді автор Suno-дан автоматты түрде анықталады.",
        "artist_invalid": "❌ Жарамсыз автор аты. Дұрыс мәтін көрсетіңіз (40 таңбаға дейін).",
        "pl_title": "Плейлист",
        "pl_total_tracks": "Барлық трек",
        "pl_tracks_word": "трек",
        "pl_prompt_action": "Әрекетті таңдаңыз:",
        "btn_pl_download": "📥 Барлық тректі жүктеу ({count})",
        "btn_pl_mix": "🎛 Плейлисттен микс жасау",
        "pl_not_found": "❌ Плейлистті жүктеу мүмкін болмады. Сілтеме дұрыс және плейлист ашық екеніне көз жеткізіңіз.",
        "pl_downloading": "📥 Плейлистті жүктеу басталды ({count} трек)...",
        "pl_mix_added": "✅ Плейлисттен <b>{count}</b> трек микс шеберіне қосылды!",
    },
}


# ─── Утилиты ───────────────────────────────────────────────────────────────────

def find_all_suno_urls(text: str) -> list[str]:
    """Находит все уникальные Suno-ссылки в тексте (до MAX_LINKS_PER_MSG)."""
    seen:   set[str]  = set()
    result: list[str] = []
    for m in URL_FINDER.finditer(text):
        url = m.group(0).rstrip(").,;!?")
        if url in seen:
            continue
        if any(d in url for d in SUNO_DOMAINS) or UUID_PATTERN.search(url):
            seen.add(url)
            result.append(url)
        if len(result) >= MAX_LINKS_PER_MSG:
            break
    return result


def extract_playlist_id(url: str) -> str | None:
    """UUID плейлиста из ссылки вида suno.com/playlist/..."""
    m = PLAYLIST_PATTERN.search(url)
    return m.group(1) if m else None


def extract_song_id(url: str) -> str | None:
    """UUID (song/...) или короткий ID (s/...) из ссылки Suno."""
    if "/playlist/" in url:
        return None
    m = UUID_PATTERN.search(url)
    if m:
        return m.group(0)
    m = SHORT_ID_PATTERN.search(url)
    if m:
        return m.group(1)
    return None


def make_suno_url(song_id: str) -> str:
    """Возвращает корректный URL Suno для UUID (/song/...) или короткого ID (/s/...)."""
    clean_id = song_id.strip()
    if UUID_PATTERN.match(clean_id):
        return f"https://suno.com/song/{clean_id}"
    return f"https://suno.com/s/{clean_id}"


def is_valid_mp3(data: bytes) -> bool:
    if len(data) < 50 * 1024:          # < 50 КБ
        return False
    if len(data) > 50 * 1024 * 1024:   # > 50 МБ (лимит Bot API)
        return False
    if data.startswith(b"ID3"):
        return True
    if len(data) > 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return True
    return False


def check_rate_limit(user_id: int) -> float:
    """0 — запрос разрешён; >0 — секунд до следующего запроса."""
    now = time.monotonic()
    remaining = RATE_LIMIT_SECONDS - (now - _user_last_request.get(user_id, 0.0))
    if remaining > 0:
        return remaining
    _user_last_request[user_id] = now
    return 0.0


def get_lang_fallback(user: types.User) -> str:
    if user.language_code:
        code = user.language_code.lower()
        if code.startswith("kk") or code.startswith("kz"):
            return "kk"
        if code.startswith("ru"):
            return "ru"
    return "en"


def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


async def _convert_audio_to_mp3(input_data: bytes | str, is_url: bool = False) -> bytes | None:
    """Конвертирует входные аудио/видео данные в MP3 через временный файл на диске.
    Запись в реальный файл позволяет libmp3lame выполнить seek и записать корректный
    Xing/VBR заголовок, благодаря чему аудиофайлы отображаются и воспроизводятся
    с точной полной длительностью в Telegram и любых медиаплеерах."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as out_tmp:
        out_path = out_tmp.name

    in_path = None
    try:
        if is_url:
            cmd = [
                "ffmpeg", "-y", "-i", str(input_data),
                "-vn", "-acodec", "libmp3lame", "-q:a", "2",
                out_path
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
        else:
            with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as in_tmp:
                in_tmp.write(input_data)
                in_path = in_tmp.name
            cmd = [
                "ffmpeg", "-y", "-i", in_path,
                "-vn", "-acodec", "libmp3lame", "-q:a", "2",
                out_path
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

        if proc.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            sz = os.path.getsize(out_path)
            if sz > 48.5 * 1024 * 1024:
                logger.info("MP3 превышает 48.5 МБ (%s байт), сжимаем с битрейтом 128k для Telegram...", sz)
                compressed_out = out_path + ".comp.mp3"
                c_proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-i", out_path,
                    "-vn", "-acodec", "libmp3lame", "-b:a", "128k",
                    compressed_out,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                await c_proc.communicate()
                if c_proc.returncode == 0 and os.path.exists(compressed_out):
                    c_sz = os.path.getsize(compressed_out)
                    if c_sz <= 49.5 * 1024 * 1024:
                        with open(compressed_out, "rb") as cf:
                            data = cf.read()
                        try:
                            os.remove(compressed_out)
                        except Exception:
                            pass
                        return data
            with open(out_path, "rb") as f:
                return f.read()
        else:
            err_msg = stderr[-300:].decode(errors="ignore") if stderr else ""
            logger.warning("Ошибка ffmpeg при конвертации в MP3: code=%s, err=%s", proc.returncode, err_msg)
            return None
    except Exception as e:
        logger.error("Исключение при конвертации в MP3: %s", e)
        return None
    finally:
        if in_path and os.path.exists(in_path):
            try:
                os.remove(in_path)
            except Exception:
                pass
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except Exception:
                pass


def add_id3_tags(
    audio_bytes: bytes,
    title: str,
    artist: str,
    image_bytes: bytes | None = None,
) -> tuple[bytes, int]:
    """Записывает ID3-теги в MP3 (название, артист, комментарий, обложка)
    и возвращает (tagged_bytes, duration_seconds).
    При ошибке возвращает исходные байты и 0."""
    buf = io.BytesIO(audio_bytes)
    duration_sec = 0
    try:
        audio = MP3(buf, ID3=ID3)
        if audio.info and audio.info.length:
            duration_sec = int(round(audio.info.length))
        if audio.tags is None:
            audio.add_tags()
        audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TPE1(encoding=3, text=artist))
        audio.tags.add(COMM(encoding=3, lang="eng", desc="", text="Downloaded with @sunosaver_bot"))
        if duration_sec > 0:
            audio.tags.add(TLEN(encoding=3, text=str(duration_sec * 1000)))
        if image_bytes:
            mime = "image/png" if image_bytes.startswith(b"\x89PNG") else "image/jpeg"
            audio.tags.add(
                APIC(
                    encoding=3,
                    mime=mime,
                    type=3,  # Cover front
                    desc="Cover",
                    data=image_bytes,
                )
            )
        buf.seek(0)
        audio.save(buf)
        buf.seek(0)
        return buf.read(), duration_sec
    except Exception as e:
        logger.warning("ID3-теги не записаны: %s", e)
        return audio_bytes, duration_sec


# ─── Клавиатуры ────────────────────────────────────────────────────────────────

def get_track_inline_keyboard(
    lang: str,
    song_id: str | None = None,
    has_lyrics: bool = True,
) -> InlineKeyboardMarkup | None:
    if not song_id:
        return None
    t = TEXTS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=t["btn_lyrics"], callback_data=f"lyrics:{song_id}"),
            InlineKeyboardButton(text=t["btn_wav"], callback_data=f"wav:{song_id}"),
        ],
        [
            InlineKeyboardButton(text=t["btn_video"], callback_data=f"video:{song_id}"),
        ],
    ])


def get_main_menu_keyboard(lang: str) -> ReplyKeyboardMarkup:
    t = TEXTS[lang]
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t["btn_create_mix"]), KeyboardButton(text=t["btn_pro"])],
            [KeyboardButton(text=t["btn_how_to"]),  KeyboardButton(text=t["btn_settings"])],
            [KeyboardButton(text=t["btn_about"]),    KeyboardButton(text=t["btn_channel"])],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def get_language_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇰🇿 Қазақша", callback_data="set_lang:kk"),
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en"),
    ]])


def get_support_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    texts = {
        "ru": "💬 Написать в поддержку",
        "kk": "💬 Қолдау қызметіне жазу",
        "en": "💬 Contact Support",
    }
    btn_text = texts.get(lang, texts["ru"])
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=btn_text, url=SUPPORT_URL)
    ]])


def get_sub_keyboard(lang: str) -> InlineKeyboardMarkup:
    t = TEXTS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_sub_channel"], url=CHANNEL_URL)],
        [InlineKeyboardButton(text=t["btn_check_sub"], callback_data="check_sub_again")]
    ])


# ─── Проверка обязательной подписки на канал ──────────────────────────────────
_sub_cache: dict[int, tuple[bool, float]] = {}
SUB_CACHE_TTL = 300  # 5 минут


async def check_user_subscription(user_id: int) -> bool:
    """Проверяет обязательную подписку пользователя на канал.
    Администратор бота всегда имеет доступ.
    Успешная подписка кэшируется на 5 минут для высокой скорости отклика."""
    if not REQUIRED_CHANNEL:
        return True
    if ADMIN_ID and user_id == ADMIN_ID:
        return True

    now = time.time()
    if user_id in _sub_cache:
        cached_sub, ts = _sub_cache[user_id]
        if cached_sub and (now - ts < SUB_CACHE_TTL):
            return True

    try:
        chat_id = int(REQUIRED_CHANNEL) if (REQUIRED_CHANNEL.startswith("-") or (REQUIRED_CHANNEL.isdigit() and len(REQUIRED_CHANNEL) > 5)) else REQUIRED_CHANNEL
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        is_sub = member.status in ("creator", "administrator", "member", "restricted")
        if is_sub:
            _sub_cache[user_id] = (True, now)
            asyncio.create_task(database.set_user_subscribed(user_id, True))
        else:
            _sub_cache.pop(user_id, None)
            asyncio.create_task(database.set_user_subscribed(user_id, False))
        return is_sub
    except Exception as e:
        err_msg = str(e).lower()
        if "member list is inaccessible" in err_msg or "chat not found" in err_msg or "bot is not a member" in err_msg:
            logger.warning("Бот не добавлен администратором в канал %s! Проверка подписки временно пропущена: %s", REQUIRED_CHANNEL, e)
            return True
        logger.warning("Ошибка проверки подписки для %s: %s", user_id, e)
        return False


class SubscriptionMiddleware(BaseMiddleware):
    """
    Глобальный middleware: строго требует подписку на канал для ВСЕХ пользователей
    (как для новых, так и для действующих). Блокирует любые сообщения, нажатия кнопок
    меню и инлайн-кнопок до момента подписки.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        user_id = user.id
        if ADMIN_ID and user_id == ADMIN_ID:
            return await handler(event, data)

        if not REQUIRED_CHANNEL:
            return await handler(event, data)

        # Разрешаем колбэки проверки подписки и выбора языка
        if isinstance(event, types.CallbackQuery):
            if event.data in ("check_sub_again",) or (event.data and event.data.startswith("set_lang:")):
                return await handler(event, data)

        # Разрешаем команду /start (чтобы зафиксировать реферала и источник трафика)
        if isinstance(event, types.Message) and event.text and event.text.startswith("/start"):
            return await handler(event, data)

        # Проверяем подписку пользователя
        is_sub = await check_user_subscription(user_id)
        if is_sub:
            return await handler(event, data)

        # Пользователь не подписан: блокируем выполнение и отправляем требование подписаться
        lang = await database.get_user_language(user_id, get_lang_fallback(user))
        t = TEXTS.get(lang, TEXTS["ru"])

        if isinstance(event, types.Message):
            await event.answer(t["sub_required"], reply_markup=get_sub_keyboard(lang), parse_mode="HTML")
            return
        elif isinstance(event, types.CallbackQuery):
            await event.answer(t["sub_failed"], show_alert=True)
            try:
                await event.message.answer(t["sub_required"], reply_markup=get_sub_keyboard(lang), parse_mode="HTML")
            except Exception:
                pass
            return


# Регистрируем middleware для всех сообщений и нажатий инлайн-кнопок
dp.message.outer_middleware(SubscriptionMiddleware())
dp.callback_query.outer_middleware(SubscriptionMiddleware())


# ─── Загрузка аудио ────────────────────────────────────────────────────────────

async def convert_and_download_mp3(
    suno_url: str,
    session:  aiohttp.ClientSession,
    retries:  int = 3,
) -> tuple[bytes | None, str]:
    """Конвертирует Suno-ссылку в MP3 через sunodownload.io.
    При 429, 5xx или таймаутах делает до `retries` повторных попыток с паузой."""
    api_url     = "https://sunodownload.io/api/suno/download/"
    payload     = {"url": suno_url, "format": "mp3", "filename": "track.mp3"}
    max_attempts = retries + 1

    for attempt in range(1, max_attempts + 1):
        try:
            async with session.post(
                api_url, json=payload, headers=POST_HEADERS,
                timeout=aiohttp.ClientTimeout(total=10), ssl=ssl_ctx,
            ) as resp:
                logger.info("sunodownload.io → HTTP %s (попытка %s/%s)", resp.status, attempt, max_attempts)
                if resp.status != 200:
                    if resp.status in (400, 404):
                        logger.info("sunodownload.io вернул HTTP %s (трек не найден или приватный)", resp.status)
                        raise TrackNotFoundError(f"sunodownload.io returned {resp.status}")
                    if resp.status == 429:
                        logger.warning("sunodownload.io возвращает 429 (лимит запросов).")
                        return None, "Suno Track"
                    if attempt < max_attempts and resp.status >= 500:
                        await asyncio.sleep(2)
                        continue
                    return None, "Suno Track"

                data          = await resp.json()
                title         = data.get("title", "Suno Track")
                download_path = data.get("downloadUrl") or data.get("path")
                if not download_path:
                    logger.warning("Нет downloadUrl/path в ответе: %s", data)
                    return None, title

                file_url = (
                    download_path if download_path.startswith("http")
                    else f"https://sunodownload.io{download_path}"
                )
                logger.info("Скачиваем аудио: %s", file_url)

                async with session.get(
                    file_url, headers=GET_HEADERS,
                    timeout=aiohttp.ClientTimeout(total=60), ssl=ssl_ctx,
                ) as fr:
                    logger.info("Файл HTTP %s, CT: %s", fr.status, fr.headers.get("Content-Type"))
                    if fr.status == 200:
                        raw = await fr.read()
                        if is_valid_mp3(raw):
                            return raw, title
                        logger.warning("Файл не прошёл проверку (размер: %s)", len(raw))
                    return None, title

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Сетевая ошибка (попытка %s/%s): %s", attempt, max_attempts, e)
            if attempt < max_attempts:
                await asyncio.sleep(2 ** attempt)
                continue
        except Exception as e:
            logger.error("Неожиданное исключение: %s", e, exc_info=True)
            break

    return None, "Suno Track"


async def try_cdn_fallback(uuid: str, session: aiohttp.ClientSession) -> tuple[bytes | None, str]:
    """Резервный способ: скачивает напрямую из CDN Suno (cdn1.suno.ai/<uuid>.mp3)."""
    cdn_url = f"https://cdn1.suno.ai/{uuid}.mp3"
    logger.info("CDN fallback: %s", cdn_url)
    try:
        async with session.get(
            cdn_url, headers=GET_HEADERS,
            timeout=aiohttp.ClientTimeout(total=60), ssl=ssl_ctx,
        ) as resp:
            logger.info("CDN fallback HTTP %s", resp.status)
            if resp.status == 200:
                data = await resp.read()
                if is_valid_mp3(data):
                    return data, "Suno Track"
    except Exception as e:
        logger.warning("CDN fallback ошибка: %s", e)
    return None, "Suno Track"


async def periodic_cache_cleanup():
    """Фоновая задача: каждые 24 ч удаляет устаревшие записи кэша."""
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            deleted = await database.cleanup_old_cache(days=CACHE_TTL_DAYS)
            if deleted:
                logger.info("Автоочистка кэша: удалено %s записей", deleted)
        except Exception as e:
            logger.error("Ошибка автоочистки: %s", e)


async def download_direct_from_suno(
    suno_url: str,
    session:  aiohttp.ClientSession,
) -> tuple[bytes | None, str, str | None, str | None]:
    """Скачивает аудио напрямую из CDN Suno и конвертирует через ffmpeg.
    1. Если есть видео MP4 — извлекает аудио напрямую.
    2. Если видео нет — запрашивает права гостя у studio-api, расшифровывает
       аудиопоток m4a и конвертирует в MP3.
    Также извлекает текст песни (lyrics / prompt).
    Не зависит от сторонних сайтов и работает автономно."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    uuid = None
    try:
        final_url = suno_url
        html_text = ""
        for page_attempt in range(1, 3):
            try:
                async with session.get(
                    suno_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15),
                    allow_redirects=True, ssl=ssl_ctx,
                ) as resp:
                    if resp.status == 404:
                        logger.info("Suno вернул HTTP 404 (трек не найден или удалён): %s", suno_url)
                        raise TrackNotFoundError(f"Suno track not found (HTTP 404): {suno_url}")
                    if resp.status == 200:
                        final_url = str(resp.url)
                        html_text = await resp.text(errors="ignore")
                        break
                    else:
                        logger.warning("Suno страница вернула HTTP %s (попытка %s/2)", resp.status, page_attempt)
            except TrackNotFoundError:
                raise
            except Exception as page_err:
                logger.warning("Ошибка запроса страницы Suno (попытка %s/2): %s", page_attempt, page_err)
            if page_attempt < 2:
                await asyncio.sleep(1.0)

        # Извлекаем UUID песни
        uuid = extract_song_id(final_url) or extract_song_id(suno_url)
        if (not uuid or not UUID_PATTERN.match(uuid)) and html_text:
            uuid_m = UUID_PATTERN.search(html_text)
            if uuid_m:
                uuid = uuid_m.group(0)

        if not uuid:
            logger.warning("Не удалось извлечь UUID из страницы Suno: %s", final_url)
            return None, "Suno Track", None, None

        # Извлекаем название трека
        title = "Suno Track"
        og_m = re.search(r'<meta property="og:title" content="([^"]+)"', html_text)
        if og_m:
            title = og_m.group(1).replace(" | Suno", "").strip()
        else:
            t_m = re.search(r'<title>(.*?)</title>', html_text)
            if t_m:
                title = t_m.group(1).replace(" | Suno", "").strip()

        # Извлекаем текст песни (lyrics / prompt) из HTML
        lyrics = None
        prompt_m = re.search(r'"prompt":"((?:[^"\\]|\\.)*)"', html_text)
        if prompt_m:
            try:
                # декодируем unicode/escape последовательности
                raw_prompt = prompt_m.group(1)
                lyrics = json.loads(f'"{raw_prompt}"').strip()
            except Exception:
                lyrics = prompt_m.group(1).encode().decode('unicode-escape', errors='ignore').strip()

        author = None
        image_url = None

        og_img = re.search(r'<meta property="og:image" content="([^"]+)"', html_text)
        if og_img:
            image_url = og_img.group(1).strip()

        author_m = re.search(r'"display_name":"([^"]+)"', html_text)
        if author_m:
            author = author_m.group(1).strip()
        else:
            handle_m = re.search(r'"handle":"([^"]+)"', html_text)
            if handle_m:
                author = handle_m.group(1).strip()
            else:
                desc_m = re.search(r'<meta property="og:description" content="[^"]*by @?([^"\.]+)', html_text)
                if desc_m:
                    author = desc_m.group(1).strip()

        # Если в HTML текст, автор или обложка не найдены, запрашиваем studio-api clip
        if uuid and (not lyrics or not author or not image_url):
            try:
                async with session.get(
                    f"https://studio-api.prod.suno.com/api/clip/{uuid}",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=5), ssl=ssl_ctx,
                ) as clip_resp:
                    if clip_resp.status == 200:
                        clip_data = await clip_resp.json()
                        if not lyrics:
                            lyrics = clip_data.get("metadata", {}).get("prompt")
                            if lyrics:
                                lyrics = lyrics.strip()
                        if not author:
                            author = clip_data.get("display_name") or clip_data.get("handle")
                        if not image_url:
                            image_url = clip_data.get("image_large_url") or clip_data.get("image_url")
            except Exception:
                pass

        if not image_url and uuid:
            image_url = f"https://cdn1.suno.ai/image_large_{uuid}.png"

        # Скачиваем байты обложки
        image_bytes = None
        if image_url:
            try:
                async with session.get(
                    image_url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=5),
                    ssl=ssl_ctx,
                ) as img_resp:
                    if img_resp.status == 200:
                        i_data = await img_resp.read()
                        if len(i_data) > 100:
                            image_bytes = i_data
            except Exception as img_err:
                logger.warning("Не удалось скачать обложку: %s", img_err)

        # 1. Проверяем наличие видео MP4 на Suno CDN
        video_m = re.search(r'"video_url":"(https://[^"]+\.mp4)"', html_text)
        if video_m:
            mp4_url = video_m.group(1)
            logger.info("Прямое скачивание с Suno CDN через видео MP4: %s", mp4_url)
            mp3_bytes = await _convert_audio_to_mp3(mp4_url, is_url=True)
            if mp3_bytes and is_valid_mp3(mp3_bytes):
                logger.info("Прямая конвертация через ffmpeg успешна: %s байт", len(mp3_bytes))
                return mp3_bytes, title, lyrics, uuid, author, image_bytes, image_url

        # 2. Прямая загрузка защищённого аудиопотока m4a через официальные гостевые права
        logger.info("Загрузка через аудиопоток m4a для трека %s...", uuid)
        rights_url = "https://studio-api.prod.suno.com/api/mango/rights"
        rights_payload = {"content_params": {"content_id": uuid, "content_type": "clip"}}
        rights_headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Origin": "https://suno.com",
            "Referer": "https://suno.com/",
        }

        rights = None
        for r_attempt in range(1, 3):
            try:
                async with session.post(
                    rights_url, json=rights_payload, headers=rights_headers,
                    timeout=aiohttp.ClientTimeout(total=10), ssl=ssl_ctx,
                ) as r_resp:
                    if r_resp.status == 200:
                        rights = await r_resp.json()
                        break
                    elif r_resp.status in (403, 404):
                        logger.info("studio-api rights вернул статус %s (трек приватный или удалён)", r_resp.status)
                        raise TrackNotFoundError(f"studio-api rights returned {r_resp.status}")
                    else:
                        logger.warning("studio-api rights вернул статус %s (попытка %s/2)", r_resp.status, r_attempt)
            except TrackNotFoundError:
                raise
            except Exception as re_err:
                logger.warning("studio-api rights ошибка сети (попытка %s/2): %s", r_attempt, re_err)
            if r_attempt < 2:
                await asyncio.sleep(1.0)

        if rights:
            glt = rights.get("glt")
            wrapped_key_b64 = rights.get("key")
            wrapped_iv_b64 = rights.get("iv")

            if glt and wrapped_key_b64 and wrapped_iv_b64:
                user_key = hashlib.sha256(glt.encode("utf-8")).digest()
                wrapped_key = base64.b64decode(wrapped_key_b64)
                wrapped_iv = base64.b64decode(wrapped_iv_b64)
                aad = uuid.encode("utf-8")

                def _decrypt_gcm(wrapped: bytes) -> bytes:
                    iv = wrapped[:12]
                    tag = wrapped[-16:]
                    ct = wrapped[12:-16]
                    d = Cipher(algorithms.AES(user_key), modes.GCM(iv, tag), backend=default_backend()).decryptor()
                    d.authenticate_additional_data(aad)
                    return d.update(ct) + d.finalize()

                content_key = _decrypt_gcm(wrapped_key)
                content_iv = _decrypt_gcm(wrapped_iv)

                # Скачиваем m4a аудиопоток с CloudFront с повторными попытками
                m4a_url = f"https://d2lwuy8qc234o3.cloudfront.net/1/clip/{uuid}.m4a"
                logger.info("Скачиваем аудиопоток Suno: %s", m4a_url)
                stream_404_count = 0
                for stream_attempt in range(1, 6):
                    try:
                        async with session.get(
                            m4a_url, headers={"User-Agent": "Mozilla/5.0"},
                            timeout=aiohttp.ClientTimeout(total=60, connect=10, sock_read=45), ssl=ssl_ctx,
                        ) as stream_resp:
                            if stream_resp.status == 200:
                                expected_len = stream_resp.headers.get("Content-Length")
                                enc_bytes = await stream_resp.read()
                                if expected_len and len(enc_bytes) < int(expected_len):
                                    logger.warning("Неполная загрузка m4a (попытка %s/5): %s из %s байт", stream_attempt, len(enc_bytes), expected_len)
                                else:
                                    cipher = Cipher(algorithms.AES(content_key), modes.CTR(content_iv), backend=default_backend())
                                    dec = cipher.decryptor()
                                    dec_bytes = dec.update(enc_bytes) + dec.finalize()

                                    # Конвертируем в MP3 с корректным Xing/VBR заголовком
                                    mp3_out = await _convert_audio_to_mp3(dec_bytes, is_url=False)
                                    if mp3_out and is_valid_mp3(mp3_out):
                                        logger.info("Успешно расшифровано и конвертировано в MP3 (попытка %s/5): %s байт", stream_attempt, len(mp3_out))
                                        return mp3_out, title, lyrics, uuid, author, image_bytes, image_url
                                    else:
                                        logger.warning("Ошибка ffmpeg при конвертации декодированного m4a (попытка %s/5)", stream_attempt)
                            elif stream_resp.status == 404:
                                stream_404_count += 1
                                logger.info("CloudFront m4a вернул 404 (трек ещё генерируется Suno, попытка %s/5)", stream_attempt)
                            else:
                                logger.warning("CloudFront m4a вернул HTTP %s (попытка %s/5)", stream_resp.status, stream_attempt)
                    except Exception as stream_err:
                        logger.warning("Ошибка сети при скачивании m4a (попытка %s/5): %s", stream_attempt, stream_err)

                    if stream_attempt < 5:
                        delay = stream_attempt * (2.5 if stream_404_count > 0 else 1.0)
                        await asyncio.sleep(delay)

                if stream_404_count >= 3:
                    # Права получены (трек существует), но CloudFront вернул 404 — трек прямо сейчас генерируется Suno
                    raise TrackStillProcessingError(f"Track {uuid} is still processing on Suno servers")

    except (TrackNotFoundError, TrackStillProcessingError):
        raise
    except Exception as e:
        logger.warning("Прямое скачивание Suno не удалось: %s", e, exc_info=True)

    return None, "Suno Track", None, uuid, author, image_bytes, image_url


async def fetch_suno_playlist(playlist_id: str, session: aiohttp.ClientSession, limit: int = 15) -> dict | None:
    """Запрашивает метаданные и список треков плейлиста Suno по UUID."""
    url = f"https://studio-api.prod.suno.com/api/playlist/{playlist_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://suno.com",
        "Referer": "https://suno.com/",
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10), ssl=ssl_ctx) as resp:
            if resp.status != 200:
                logger.warning("studio-api playlist вернул статус %s для %s", resp.status, playlist_id)
                return None
            data = await resp.json()
            title = data.get("name") or "Suno Playlist"
            creator = data.get("user_display_name") or data.get("user_handle") or "Suno Creator"
            total = data.get("num_total_results") or len(data.get("playlist_clips", []))
            image_url = data.get("image_url")
            clips_raw = data.get("playlist_clips", [])
            tracks = []
            for item in clips_raw[:limit]:
                clip = item.get("clip", {})
                c_id = clip.get("id")
                if c_id:
                    tracks.append({
                        "song_id": c_id,
                        "title": clip.get("title") or "Suno Track",
                        "author": clip.get("display_name") or clip.get("handle") or creator,
                        "url": f"https://suno.com/song/{c_id}",
                    })
            return {
                "id": playlist_id,
                "title": title,
                "creator": creator,
                "total": total,
                "image_url": image_url,
                "tracks": tracks,
            }
    except Exception as e:
        logger.error("Ошибка получения плейлиста %s: %s", playlist_id, e)
        return None


def get_playlist_inline_keyboard(lang: str, playlist_id: str, tracks_count: int) -> InlineKeyboardMarkup:
    t = TEXTS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_pl_download"].format(count=tracks_count), callback_data=f"pl_dl:{playlist_id}")],
        [InlineKeyboardButton(text=t["btn_pl_mix"], callback_data=f"pl_mix:{playlist_id}")],
    ])


# ─── Обработка одного трека ────────────────────────────────────────────────────

async def _download_and_send(
    message:   types.Message,
    suno_url:  str,
    lang:      str,
    t:         dict,
    track_num: int | None = None,
    total:     int | None = None,
) -> bool:
    """Скачивает и отправляет один трек. Возвращает True при успехе."""
    user_id = message.from_user.id

    # ── Проверка дневного лимита скачиваний ───────────────────────────────────
    is_pro = await database.is_user_pro(user_id, admin_id=ADMIN_ID)
    allowed, current_dl = await database.check_daily_limit(user_id, "downloads", FREE_DAILY_DOWNLOADS, is_pro)
    if not allowed:
        ref_url = f"https://t.me/sunosaver_bot?start=ref_{user_id}"
        await message.answer(
            t["limit_downloads_reached"].format(limit=FREE_DAILY_DOWNLOADS, ref_url=ref_url),
            parse_mode="HTML"
        )
        return False

    song_id = extract_song_id(suno_url)
    custom_artist = await database.get_user_custom_artist(user_id)

    # ── Кэш ──────────────────────────────────────────────────────────────────
    if song_id:
        cache_data = await database.get_cached_track(song_id)
        if cache_data:
            cached_fid = cache_data[0]
            cached_title = cache_data[1]
            cached_lyrics = cache_data[2]
            cached_artist = cache_data[3] if len(cache_data) > 3 else None
            safe_title = cached_title or "Suno Track"
            escaped_title = html.escape(safe_title)
            artist = custom_artist or cached_artist or "Suno AI (@sunosaver_bot)"
            caption = f"🎵 <b>{escaped_title}</b>\n{t['artist_label']}: {artist}"
            logger.info("Из кэша: %s", song_id)
            try:
                reply_markup = get_track_inline_keyboard(lang, song_id)
                await message.answer_audio(
                    audio=cached_fid,
                    caption=caption,
                    title=safe_title,
                    performer=artist,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
                await database.increment_total_downloads()
                await database.increment_daily_usage(user_id, "downloads")
                await database.save_user_track(user_id, song_id, safe_title)
                return True
            except Exception as e:
                logger.warning("Кэшированный file_id устарел, перекачиваем: %s", e)

    # ── Статусное сообщение ───────────────────────────────────────────────────
    prefix     = f"[{track_num}/{total}] " if total and total > 1 else ""
    status_msg = await message.answer(f"{prefix}{t['fetching']}", parse_mode="HTML")
    delete_status = False

    try:
        # ── 1. Прямая загрузка с Suno через ffmpeg (основной метод) ───────────
        raw_audio = None
        title = "Suno Track"
        lyrics = None
        resolved_uuid = None
        author = None
        image_bytes = None
        image_url = None
        is_not_found = False
        try:
            async with SEMAPHORE:
                res = await download_direct_from_suno(suno_url, HTTP_SESSION)
                raw_audio, title, lyrics, resolved_uuid, author, image_bytes, image_url = res
        except TrackNotFoundError:
            is_not_found = True

        # Если первая попытка вернула None (но не 404), пробуем ещё раз с короткой паузой
        if not raw_audio and not is_not_found:
            logger.info("Повторная попытка прямой загрузки Suno через 1.5 сек...")
            await asyncio.sleep(1.5)
            try:
                async with SEMAPHORE:
                    res = await download_direct_from_suno(suno_url, HTTP_SESSION)
                    raw_audio, title, lyrics, resolved_uuid, author, image_bytes, image_url = res
            except TrackNotFoundError:
                is_not_found = True

        # ── 2. Резерв через CDN fallback (cdn1.suno.ai) если знаем UUID ────────
        target_uuid = resolved_uuid or (song_id if (song_id and UUID_PATTERN.match(song_id)) else None)
        if not raw_audio and not is_not_found and target_uuid:
            logger.info("Пробуем прямой CDN fallback (cdn1.suno.ai) для %s", target_uuid)
            try:
                async with SEMAPHORE:
                    cdn_audio, _ = await try_cdn_fallback(target_uuid, HTTP_SESSION)
                    if cdn_audio:
                        raw_audio = cdn_audio
            except Exception as cdn_err:
                logger.warning("Ошибка CDN fallback: %s", cdn_err)

        # ── 3. Резерв через sunodownload.io если остальные методы не сработали ──
        if not raw_audio and not is_not_found:
            logger.info("Пробуем резервный метод через sunodownload.io")
            try:
                async with SEMAPHORE:
                    raw_audio, title = await convert_and_download_mp3(suno_url, HTTP_SESSION)
            except TrackNotFoundError:
                is_not_found = True

        if not raw_audio:
            if is_not_found:
                err_text = t.get("error_track_not_found", t["error_download"]) + t.get("error_contact", "")
                await status_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
            else:
                err_text = t["error_download"] + t.get("error_contact", "")
                await status_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
                await notify_admin_error("_download_and_send:download_failed", Exception("Не удалось скачать трек через Suno CDN и sunodownload.io"), f"URL: {suno_url}", user=message.from_user)
            return False

        original_song_id = song_id
        if resolved_uuid:
            song_id = resolved_uuid

        try:
            await status_msg.edit_text(f"{prefix}{t['downloading']}", parse_mode="HTML")
        except Exception:
            pass

        # ── Формирование файла ────────────────────────────────────────────────
        safe_title     = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Suno Track"
        escaped_title  = html.escape(safe_title)
        artist         = custom_artist or author or "Suno AI (@sunosaver_bot)"
        tagged_audio, duration_sec = add_id3_tags(raw_audio, safe_title, artist, image_bytes=image_bytes)
        audio_file     = BufferedInputFile(tagged_audio, filename=f"{safe_title}.mp3")
        thumb_file     = BufferedInputFile(image_bytes, filename="cover.jpg") if image_bytes else None
        caption        = f"🎵 <b>{escaped_title}</b>\n{t['artist_label']}: {artist}"
        reply_markup   = get_track_inline_keyboard(lang, song_id)

        # ── Отправка ──────────────────────────────────────────────────────────
        sent_msg = await message.answer_audio(
            audio=audio_file,
            thumbnail=thumb_file,
            caption=caption,
            title=safe_title,
            performer=artist,
            duration=duration_sec if duration_sec > 0 else None,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

        if sent_msg.audio:
            if song_id:
                await database.save_track_cache(song_id, sent_msg.audio.file_id, safe_title, lyrics, artist=author, image_url=image_url)
            if original_song_id and original_song_id != song_id:
                await database.save_track_cache(original_song_id, sent_msg.audio.file_id, safe_title, lyrics, artist=author, image_url=image_url)
            await database.save_user_track(message.from_user.id, song_id or original_song_id, safe_title)
            await database.increment_daily_usage(message.from_user.id, "downloads")

        delete_status = True
        return True

    except TrackNotFoundError as e:
        try:
            err_text = t.get("error_track_not_found", t["error_download"]) + t.get("error_contact", "")
            await status_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
        except Exception:
            pass
        return False
    except TrackStillProcessingError as e:
        try:
            err_text = t.get("error_track_processing", "⏳ Трек ещё генерируется или обрабатывается серверами Suno.\nПожалуйста, подождите 20–30 секунд и отправьте ссылку снова!") + t.get("error_contact", "")
            await status_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
            logger.info("Трек ещё генерируется Suno: %s", suno_url)
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error("Ошибка пайплайна: %s", e, exc_info=True)
        await notify_admin_error("_download_and_send:exception", e, f"URL: {suno_url}", user=message.from_user)
        err_text = t["error_telegram"] + t.get("error_contact", "")
        try:
            await status_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
        except Exception:
            await message.answer(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
        return False

    finally:
        if delete_status:
            try:
                await status_msg.delete()
            except Exception:
                pass


# ─── Команды ───────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    fallback_lang = get_lang_fallback(message.from_user)

    referrer_id = None
    source = "direct"
    if command.args:
        raw_arg = command.args.strip()
        if raw_arg.startswith("ref_"):
            raw_id = raw_arg[4:]
            if raw_id.isdigit():
                referrer_id = int(raw_id)
                source = "referral"
        elif raw_arg.isdigit():
            referrer_id = int(raw_arg)
            source = "referral"
        else:
            clean_arg = re.sub(r'[^a-zA-Z0-9_\-]', '', raw_arg)[:32]
            source = clean_arg if clean_arg else "direct"

    lang, is_new, effective_ref = await database.register_or_get_user(
        user_id, fallback_lang, referrer_id, source=source
    )

    # Если это новый пользователь и у него есть действительный реферер
    if is_new and effective_ref:
        new_count, became_pro = await database.add_referral_and_check_pro(
            effective_ref, required_referrals=REFERRALS_FOR_PRO
        )
        ref_lang = await database.get_user_language(effective_ref, "ru")
        ref_t = TEXTS[ref_lang]
        try:
            if became_pro:
                await bot.send_message(
                    chat_id=effective_ref,
                    text=ref_t["ref_pro_unlocked"].format(total=REFERRALS_FOR_PRO),
                    parse_mode="HTML"
                )
            else:
                remaining = max(0, REFERRALS_FOR_PRO - new_count)
                await bot.send_message(
                    chat_id=effective_ref,
                    text=ref_t["ref_progress"].format(
                        invited=new_count, total=REFERRALS_FOR_PRO, remaining=remaining
                    ),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.warning("Не удалось уведомить реферера %s: %s", effective_ref, e)

    if is_new:
        asyncio.create_task(check_and_trigger_100_promo())

    if not await check_user_subscription(user_id):
        await message.answer(TEXTS[lang]["sub_required"], reply_markup=get_sub_keyboard(lang), parse_mode="HTML")
        return

    await message.answer(TEXTS[lang]["start"], reply_markup=get_main_menu_keyboard(lang), parse_mode="HTML")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["help"], parse_mode="HTML")


@dp.message(Command("settings"))
async def cmd_settings(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["settings"], reply_markup=get_language_inline_keyboard(), parse_mode="HTML")


@dp.message(F.text.in_([t["btn_pro"] for t in TEXTS.values()]))
@dp.message(Command("pro"))
@dp.message(Command("ref"))
async def cmd_pro_referral(message: types.Message):
    import urllib.parse
    user_id = message.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(message.from_user))
    t = TEXTS[lang]

    pro_info = await database.get_user_pro_info(user_id, admin_id=ADMIN_ID, required_referrals=REFERRALS_FOR_PRO)
    ref_url = f"https://t.me/sunosaver_bot?start=ref_{user_id}"

    # Быстрый шеринг в Telegram
    share_text = t["ref_share_text"].format(ref_url=ref_url)
    share_tg_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_url)}&text={urllib.parse.quote(share_text)}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_share_ref"], url=share_tg_url)],
    ])

    if pro_info["is_pro"]:
        text = t["pro_active_text"].format(
            ref_url=ref_url,
            invited=pro_info["invited_count"],
        )
    else:
        text = t["pro_promo_text"].format(
            ref_url=ref_url,
            invited=pro_info["invited_count"],
            total=REFERRALS_FOR_PRO,
            needed=pro_info["needed"],
            dl_today=pro_info["downloads_today"],
            dl_max=FREE_DAILY_DOWNLOADS,
            mix_today=pro_info["mixes_today"],
            mix_max=FREE_DAILY_MIXES,
            wav_today=pro_info["wav_today"],
            wav_max=FREE_DAILY_WAV,
        )

    await message.answer(text, reply_markup=kb, parse_mode="HTML", disable_web_page_preview=True)


# ─── Праздничное промо: 100 пользователей ────────────────────────────────────

PROMO_100_TEXTS = {
    "ru": (
        "🎉 <b>Ура! Нас уже 100 пользователей!</b>\n\n"
        "Огромное спасибо каждому из вас за то, что пользуетесь SunoSaver! ❤️\n\n"
        "В честь этой крутой отметки мы дарим вам <b>навсегда статус PRO</b>! ⭐️\n\n"
        "<b>Что теперь открыто на вашем аккаунте:</b>\n"
        "🚀 <b>Безлимитные</b> скачивания треков каждый день\n"
        "🎼 Скачивание аудио в студийном качестве <b>WAV (HD)</b>\n"
        "🎬 Выгрузка <b>MP4 видео</b> с визуализацией трека\n"
        "🎛 Создание миксов <b>до 10 песен</b> с плавным DJ Crossfade\n\n"
        "Ваш PRO-аккаунт уже активирован! Творите и создавайте шедевры без ограничений 🎧"
    ),
    "en": (
        "🎉 <b>Hooray! We've reached 100 users!</b>\n\n"
        "A huge thank you to each of you for using SunoSaver! ❤️\n\n"
        "To celebrate this milestone, we are gifting you a <b>lifetime PRO status</b>! ⭐️\n\n"
        "<b>What's unlocked on your account:</b>\n"
        "🚀 <b>Unlimited</b> daily track downloads\n"
        "🎼 Studio-quality lossless <b>WAV (HD)</b> downloads\n"
        "🎬 <b>MP4 video</b> exports with track visualizer\n"
        "🎛 Seamless DJ mix builder for up to <b>10 tracks</b>\n\n"
        "Your PRO status is already active! Enjoy creating great music without limits 🎧"
    ),
    "kk": (
        "🎉 <b>Керемет жаңалық! Біз 100 қолданушыға жеттік!</b>\n\n"
        "SunoSaver-ді таңдағаныңыз үшін әрқайсыңызға үлкен алғыс! ❤️\n\n"
        "Осы маңызды меже құрметіне біз сізге <b>PRO мәртебесін мәңгіге сыйлаймыз</b>! ⭐️\n\n"
        "<b>Сіздің аккаунтыңызда ашылған мүмкіндіктер:</b>\n"
        "🚀 Күніне <b>шектеусіз</b> трек жүктеу\n"
        "🎼 Студиялық таза <b>WAV (HD)</b> дыбыс сапасы\n"
        "🎬 Визуализациясы бар <b>MP4 бейнеклиптер</b>\n"
        "🎛 <b>10 әнге дейін</b> DJ Crossfade арқылы микс жасау\n\n"
        "PRO-мәртебеңіз белсендірілді! Шектеусіз шығармашылық шабыт тілейміз 🎧"
    ),
}

_promo_100_lock = asyncio.Lock()


async def check_and_trigger_100_promo(force: bool = False) -> tuple[bool, int, int]:
    """
    Проверяет, достигнута ли отметка в 100 пользователей, и если да —
    выдает первым 100 пользователям вечный PRO и отправляет рассылку.
    Возвращает (was_triggered, pro_count, sent_count).
    """
    async with _promo_100_lock:
        if not force and await database.is_promo_100_awarded():
            return False, 0, 0

        total_users = await database.get_total_users_count()
        if not force and total_users < 100:
            return False, 0, 0

        logger.info("🎉 Достигнута отметка 100 пользователей! Запуск промо-рассылки...")

        # 1. Выдаем PRO первым 100 пользователям
        pro_count = await database.award_pro_to_first_n_users(100)

        # 2. Получаем список первых 100 пользователей с их языками
        users = await database.get_first_n_users_with_lang(100)
        sent_count = 0

        for uid, lang in users:
            msg_text = PROMO_100_TEXTS.get(lang, PROMO_100_TEXTS["ru"])
            try:
                await bot.send_message(chat_id=uid, text=msg_text, parse_mode="HTML")
                sent_count += 1
                await asyncio.sleep(0.05)  # не спамим Telegram API
            except TelegramForbiddenError:
                pass
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                try:
                    await bot.send_message(chat_id=uid, text=msg_text, parse_mode="HTML")
                    sent_count += 1
                except Exception:
                    pass
            except Exception as e:
                logger.warning("Ошибка отправки промо-сообщения пользователю %s: %s", uid, e)

        # 3. Уведомляем администратора
        if ADMIN_ID:
            try:
                await bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"🎉 <b>Событие: Нас 100 пользователей!</b>\n\n"
                        f"⭐️ Первым 100 пользователям успешно выдан <b>вечный PRO</b>!\n"
                        f"📩 Рассылка доставлена: <b>{sent_count} / {len(users)}</b> пользователям."
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error("Не удалось уведомить админа о 100 пользователях: %s", e)

        return True, pro_count, sent_count


# ─── Команды администратора ────────────────────────────────────────────────────

@dp.message(Command("promo100"))
async def cmd_promo100(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    force = len(parts) > 1 and parts[1].lower() in ("force", "run", "now")

    is_awarded = await database.is_promo_100_awarded()
    total_users = await database.get_total_users_count()

    if not force:
        status_str = "✅ Уже проведена" if is_awarded else f"⏳ Ожидает ({total_users}/100 пользователей)"
        await message.answer(
            f"🎁 <b>Промо-акция «Первые 100 пользователей — PRO навсегда»:</b>\n\n"
            f"• Статус: <b>{status_str}</b>\n"
            f"• Пользователей в базе: <b>{total_users}</b>\n\n"
            f"<i>Для принудительного запуска прямо сейчас отправьте:</i> <code>/promo100 force</code>",
            parse_mode="HTML",
        )
        return

    await message.answer("⏳ Запускаю выдачу PRO и рассылку первым 100 пользователям...", parse_mode="HTML")
    triggered, pro_count, sent_count = await check_and_trigger_100_promo(force=True)
    await message.answer(
        f"✅ <b>Промо-рассылка завершена!</b>\n\n"
        f"⭐️ Всего пользователей с PRO: <b>{pro_count}</b>\n"
        f"📩 Доставлено поздравлений: <b>{sent_count}</b>",
        parse_mode="HTML",
    )

@dp.message(Command("admin"))
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    s = await database.get_stats()

    # Запрашиваем актуальное количество участников в канале через API Telegram
    channel_info = None
    if REQUIRED_CHANNEL:
        try:
            chat_id = int(REQUIRED_CHANNEL) if (REQUIRED_CHANNEL.startswith("-") or (REQUIRED_CHANNEL.isdigit() and len(REQUIRED_CHANNEL) > 5)) else REQUIRED_CHANNEL
            curr_count = await bot.get_chat_member_count(chat_id=chat_id)
            channel_info = await database.record_channel_subscriber_count(curr_count)
        except Exception as e:
            logger.warning("Не удалось получить число участников канала: %s", e)

    # 1. Секция канала и обязательной подписки
    channel_block = ""
    if channel_info:
        gain_sign = "+" if channel_info["gained_total"] >= 0 else ""
        gain_today_sign = "+" if channel_info["gained_today"] >= 0 else ""
        sub_pct = round((s["subscribed_users"] / s["total_users"] * 100), 1) if s["total_users"] > 0 else 0
        channel_block = (
            f"📢 <b>Канал {REQUIRED_CHANNEL}:</b>\n"
            f"• Всего в канале: <b>{channel_info['current']:,}</b> подписчиков\n"
            f"• Прирост подписчиков канала: <b>{gain_sign}{channel_info['gained_total']:,}</b> (за сегодня: <b>{gain_today_sign}{channel_info['gained_today']:,}</b>)\n"
            f"• Подписано пользователей бота: <b>{s['subscribed_users']:,}</b> ({sub_pct}% базы)\n"
            f"• Подписалось за последние 24ч: <b>+{s['subscribed_24h']:,}</b>\n\n"
        )

    # 2. Реальные и неактивные пользователи
    total_users = s["total_users"]
    real_users = s["real_users"]
    real_pct = round((real_users / total_users * 100), 1) if total_users > 0 else 0
    inactive_users = max(0, total_users - real_users)
    inactive_pct = round((inactive_users / total_users * 100), 1) if total_users > 0 else 0

    # 3. Источники трафика
    source_names = {
        "direct": "🔍 Прямой поиск / Органика",
        "referral": "👥 Реферальная программа",
    }
    source_lines = []
    for item in s.get("sources", []):
        src_code = item["source"]
        name = source_names.get(src_code, f"🔗 {src_code}")
        source_lines.append(
            f"  • {name}: <b>{item['count']}</b> ({item['percent']}%) "
            f"| реальных: <b>{item['real_count']}</b> ({item['real_percent']}%)"
        )
    source_text = "\n".join(source_lines) if source_lines else "  • —"

    # 4. Языки
    lang_map = {
        "ru": ("🇷🇺", "Русский"),
        "kk": ("🇰🇿", "Қазақша"),
        "en": ("🇬🇧", "English"),
    }
    lang_lines = []
    for item in s.get("languages", []):
        code = item["lang"]
        flag, name = lang_map.get(code, ("🌐", code.upper()))
        lang_lines.append(f"  {flag} {name}: <b>{item['count']}</b> ({item['percent']}%)")
    lang_text = "\n".join(lang_lines) if lang_lines else "  —"

    text = (
        f"📊 <b>Аналитика & Статистика SunoSaver</b>\n\n"
        f"{channel_block}"
        f"👥 <b>Пользователи бота:</b>\n"
        f"• Всего пользователей: <b>{total_users:,}</b>\n"
        f"• Реальных (скачивали треки): <b>{real_users:,}</b> ({real_pct}%)\n"
        f"• Неактивных (только /start): <b>{inactive_users:,}</b> ({inactive_pct}%)\n"
        f"• Новых за 24 часа: <b>+{s['new_users_24h']:,}</b>\n"
        f"• Активных сегодня: <b>{s['active_users_today']:,}</b>\n"
        f"• Активных за 7 дней: <b>{s['active_users_7d']:,}</b>\n"
        f"• PRO-аккаунтов: <b>{s['pro_users']:,}</b>\n"
        f"• Заблокировано: <b>{s['banned_users']:,}</b>\n\n"
        f"📍 <b>Откуда пришли пользователи (Источники):</b>\n"
        f"{source_text}\n\n"
        f"🌍 <b>Языки аудитории:</b>\n"
        f"{lang_text}\n\n"
        f"⚡️ <b>Активность за сегодня:</b>\n"
        f"• Скачиваний MP3: <b>{s['downloads_today']:,}</b>\n"
        f"• Создано миксов: <b>{s['mixes_today']:,}</b>\n"
        f"• Конвертаций в WAV: <b>{s['wav_today']:,}</b>\n\n"
        f"💾 <b>Кэш и База данных:</b>\n"
        f"• Скачано треков за всё время: <b>{s['total_downloads']:,}</b>\n"
        f"• Создано миксов за всё время: <b>{s['total_mixes']:,}</b>\n"
        f"• Сконвертировано WAV за всё время: <b>{s['total_wavs']:,}</b>\n"
        f"• В кэше MP3 треков: <b>{s['cached_tracks']:,}</b>\n"
        f"• В кэше MP4 (видео): <b>{s['cached_videos']:,}</b>\n"
        f"• В кэше WAV (HD): <b>{s['cached_wavs']:,}</b>"
    )
    await message.answer(text, parse_mode="HTML")


@dp.message(Command("ban"))
async def cmd_ban(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("❌ Использование: <code>/ban &lt;user_id&gt; [причина]</code>", parse_mode="HTML")
        return
    target_id = int(parts[1])
    reason    = parts[2] if len(parts) > 2 else ""
    await database.ban_user(target_id, reason)
    await message.answer(
        f"✅ Пользователь <code>{target_id}</code> заблокирован.\nПричина: {reason or '—'}",
        parse_mode="HTML",
    )
    logger.info("Админ заблокировал %s. Причина: %s", target_id, reason)


@dp.message(Command("unban"))
async def cmd_unban(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("❌ Использование: <code>/unban &lt;user_id&gt;</code>", parse_mode="HTML")
        return
    target_id = int(parts[1])
    await database.unban_user(target_id)
    await message.answer(
        f"✅ Пользователь <code>{target_id}</code> разблокирован.",
        parse_mode="HTML",
    )
    logger.info("Админ разблокировал %s", target_id)


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    text_to_send = None
    is_reply = False
    if message.reply_to_message:
        is_reply = True
    else:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            text_to_send = parts[1]
        else:
            await message.answer(
                "📢 <b>Использование рассылки (/broadcast):</b>\n\n"
                "1️⃣ <b>Ответом на сообщение:</b> отправьте в чат пост (текст, фото, видео, голосовое, кнопки) и ответьте на него командой <code>/broadcast</code>.\n"
                "2️⃣ <b>Текстом:</b> <code>/broadcast &lt;текст сообщения&gt;</code>",
                parse_mode="HTML"
            )
            return

    users = await database.get_all_users()
    total = len(users)
    if total == 0:
        await message.answer("ℹ️ В базе нет пользователей для рассылки.")
        return

    status_msg = await message.answer(
        f"⏳ Начинаю рассылку для <b>{total}</b> пользователей...",
        parse_mode="HTML"
    )

    sent = 0
    blocked = 0
    failed = 0
    start_time = time.time()

    for idx, uid in enumerate(users, 1):
        try:
            if is_reply:
                await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=message.chat.id,
                    message_id=message.reply_to_message.message_id
                )
            else:
                await bot.send_message(
                    chat_id=uid,
                    text=text_to_send,
                    parse_mode="HTML"
                )
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                if is_reply:
                    await bot.copy_message(
                        chat_id=uid,
                        from_chat_id=message.chat.id,
                        message_id=message.reply_to_message.message_id
                    )
                else:
                    await bot.send_message(
                        chat_id=uid,
                        text=text_to_send,
                        parse_mode="HTML"
                    )
                sent += 1
            except Exception:
                failed += 1
        except Exception as e:
            failed += 1
            logger.warning("Ошибка отправки сообщения пользователю %s при рассылке: %s", uid, e)

        await asyncio.sleep(0.05)

        if idx % 25 == 0 or idx == total:
            try:
                await status_msg.edit_text(
                    f"📢 <b>Рассылка в процессе...</b>\n\n"
                    f"👥 Прогресс: {idx}/{total}\n"
                    f"✅ Доставлено: {sent}\n"
                    f"🚫 Заблокировали: {blocked}\n"
                    f"❌ Ошибок: {failed}",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    duration = time.time() - start_time
    await status_msg.edit_text(
        f"✅ <b>Рассылка успешно завершена!</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"📨 Успешно доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"❌ Ошибок доставки: <b>{failed}</b>\n"
        f"⏱ Время выполнения: <b>{duration:.1f} сек</b>",
        parse_mode="HTML"
    )
    logger.info("Рассылка завершена: sent=%s, blocked=%s, failed=%s, duration=%.1fs", sent, blocked, failed, duration)


@dp.message(Command("backup"))
async def cmd_backup(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    db_path = database.DB_NAME
    if not os.path.exists(db_path):
        await message.answer("❌ Файл базы данных не найден.")
        return

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    filename = f"backup_bot_data_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
    db_file = FSInputFile(db_path, filename=filename)

    await message.answer_document(
        document=db_file,
        caption=f"💾 <b>Резервная копия базы данных</b>\n📅 <code>{now_str}</code>\n⚡️ @sunosaver_bot",
        parse_mode="HTML"
    )


async def periodic_db_backup():
    """Фоновая задача: каждые 24 часа отправляет бэкап базы данных администратору."""
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            db_path = database.DB_NAME
            if ADMIN_ID and os.path.exists(db_path):
                now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                filename = f"backup_bot_data_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
                db_file = FSInputFile(db_path, filename=filename)
                await bot.send_document(
                    chat_id=ADMIN_ID,
                    document=db_file,
                    caption=f"💾 <b>Автоматический бэкап базы данных (24ч)</b>\n📅 <code>{now_str}</code>\n⚡️ @sunosaver_bot",
                    parse_mode="HTML"
                )
                logger.info("Автобэкап базы данных отправлен админу %s", ADMIN_ID)
        except Exception as e:
            logger.error("Ошибка автобэкапа базы данных: %s", e, exc_info=True)
            await notify_admin_error("periodic_db_backup", e)


# ─── Кнопки меню ───────────────────────────────────────────────────────────────

@dp.message(F.text.in_([t["btn_how_to"] for t in TEXTS.values()]))
async def btn_help(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["help"], parse_mode="HTML")


@dp.message(F.text.in_([t["btn_settings"] for t in TEXTS.values()]))
async def btn_settings(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["settings"], reply_markup=get_language_inline_keyboard(), parse_mode="HTML")


@dp.message(Command("about"))
@dp.message(F.text.in_([t["btn_about"] for t in TEXTS.values()]))
async def btn_about(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["about"], parse_mode="HTML")


@dp.message(F.text.in_([t["btn_channel"] for t in TEXTS.values()]))
async def btn_channel(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    t  = TEXTS[lang]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t["btn_go_channel"], url=CHANNEL_URL)
    ]])
    await message.answer(t["channel_msg"], reply_markup=kb, parse_mode="HTML")


# ─── Настройка авторства (/artist) ─────────────────────────────────────────────

@dp.message(Command("artist"))
@dp.message(Command("author"))
async def cmd_artist(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(message.from_user))
    t = TEXTS.get(lang, TEXTS["ru"])
    if not command.args or not command.args.strip():
        current_artist = await database.get_user_custom_artist(user_id)
        current_str = html.escape(current_artist) if current_artist else t["artist_auto"]
        await message.answer(t["artist_info"].format(current=current_str), parse_mode="HTML")
        return
    arg = command.args.strip()
    if arg.lower() in ("reset", "clear", "сброс", "auto"):
        await database.set_user_custom_artist(user_id, None)
        await message.answer(t["artist_reset_done"], parse_mode="HTML")
    else:
        clean_name = re.sub(r'[\\/*?:"<>|\n\r\t]', '', arg).strip()[:40]
        if not clean_name:
            await message.answer(t["artist_invalid"], parse_mode="HTML")
            return
        await database.set_user_custom_artist(user_id, clean_name)
        await message.answer(t["artist_set_done"].format(artist=html.escape(clean_name)), parse_mode="HTML")


# ─── Смена языка ───────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("set_lang:"))
async def handle_language_selection(callback: CallbackQuery):
    lang_map = {"set_lang:ru": "ru", "set_lang:en": "en", "set_lang:kk": "kk"}
    new_lang = lang_map.get(callback.data)
    if not new_lang:
        await callback.answer("Unknown language", show_alert=True)
        return

    await database.set_user_language(callback.from_user.id, new_lang)
    t = TEXTS[new_lang]
    await callback.answer()
    await callback.message.edit_text(
        t["lang_changed"], reply_markup=get_language_inline_keyboard(), parse_mode="HTML"
    )
    await callback.message.answer(
        t["start"], reply_markup=get_main_menu_keyboard(new_lang), parse_mode="HTML"
    )


@dp.callback_query(F.data == "check_sub_again")
async def handle_check_sub_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]
    _sub_cache.pop(user_id, None)
    is_sub = await check_user_subscription(user_id)
    if is_sub:
        await database.set_user_subscribed(user_id, True)
        await callback.answer(t["sub_success"], show_alert=True)
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            t["start"], reply_markup=get_main_menu_keyboard(lang), parse_mode="HTML"
        )
    else:
        await database.set_user_subscribed(user_id, False)
        await callback.answer(t["sub_failed"], show_alert=True)


# ─── Текст песни (Lyrics Callback) ─────────────────────────────────────────────

@dp.callback_query(F.data.startswith("lyrics:"))
async def handle_lyrics_callback(callback: CallbackQuery):
    song_id = callback.data.split(":", 1)[1]
    lang = await database.get_user_language(callback.from_user.id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]

    # 1. Проверяем кэш базы данных
    lyrics = await database.get_track_lyrics(song_id)
    uuid = song_id

    # 2. Если в БД нет текста, пробуем разрешить короткий ID и получить текст с Suno
    if not lyrics:
        if not UUID_PATTERN.match(uuid):
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                }
                async with HTTP_SESSION.get(
                    f"https://suno.com/s/{song_id}", headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True, ssl=ssl_ctx,
                ) as r_suno:
                    final_url = str(r_suno.url)
                    found_uuid = extract_song_id(final_url)
                    if found_uuid and UUID_PATTERN.match(found_uuid):
                        uuid = found_uuid
                    else:
                        html_t = await r_suno.text(errors="ignore")
                        u_m = UUID_PATTERN.search(html_t)
                        if u_m:
                            uuid = u_m.group(0)

                    # Проверяем наличие prompt прямо в HTML
                    html_t = await r_suno.text(errors="ignore")
                    prompt_m = re.search(r'"prompt":"((?:[^"\\]|\\.)*)"', html_t)
                    if prompt_m:
                        try:
                            lyrics = json.loads(f'"{prompt_m.group(1)}"').strip()
                        except Exception:
                            lyrics = prompt_m.group(1).strip()
            except Exception as e:
                logger.warning("Не удалось разрешить ID для текста: %s", e)

        # Если ещё не нашли текст, пробуем studio-api clip
        if not lyrics and uuid:
            try:
                async with HTTP_SESSION.get(
                    f"https://studio-api.prod.suno.com/api/clip/{uuid}",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=5), ssl=ssl_ctx,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        lyrics = data.get("metadata", {}).get("prompt")
                        if lyrics:
                            lyrics = lyrics.strip()
            except Exception as e:
                logger.warning("Ошибка запроса studio-api clip для lyrics: %s", e)

        # Сохраняем найденный текст в БД
        if lyrics:
            try:
                cached = await database.get_cached_track(song_id)
                cached_title = cached[1] if cached else "Suno Track"
                cached_fid = cached[0] if cached else ""
                await database.save_track_cache(song_id, cached_fid, cached_title, lyrics)
                if uuid and uuid != song_id:
                    await database.save_track_cache(uuid, cached_fid, cached_title, lyrics)
            except Exception as e:
                logger.warning("Не удалось закэшировать текст: %s", e)

    if not lyrics:
        await callback.answer(t["lyrics_none"], show_alert=True)
        return

    await callback.answer()
    title = "Suno Track"
    cached = await database.get_cached_track(song_id)
    if cached and cached[1]:
        title = cached[1]

    # Если текст очень длинный (>4000 символов), разбиваем на части
    escaped_title = html.escape(title)
    escaped_lyrics = html.escape(lyrics)
    template = t.get("lyrics_title", "📜 <b>«{title}»:</b>\n\n<blockquote>{lyrics}</blockquote>")
    if "<blockquote>" not in template:
        template = template.replace("{lyrics}", "<blockquote>{lyrics}</blockquote>")
    msg_text = template.format(title=escaped_title, lyrics=escaped_lyrics)

    if len(msg_text) > 4000:
        msg_text = msg_text[:3950] + "...\n</blockquote>"

    await callback.message.reply(msg_text, parse_mode="HTML")


# ─── Конструктор миксов (Suno Mix Maker) ───────────────────────────────────────

_user_mix_queues: dict[int, list[dict]] = {}
_user_mix_pages:  dict[int, int] = {}
MIX_PAGE_SIZE = 8


async def render_mix_view(user_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """Генерирует текст и инлайн-клавиатуру конструктора миксов с пагинацией библиотеки."""
    t = TEXTS[lang]
    queue = _user_mix_queues.get(user_id, [])

    total_tracks = await database.get_user_tracks_count(user_id)
    total_pages = max(1, (total_tracks + MIX_PAGE_SIZE - 1) // MIX_PAGE_SIZE)
    cur_page = _user_mix_pages.get(user_id, 0)
    if cur_page >= total_pages:
        cur_page = max(0, total_pages - 1)
        _user_mix_pages[user_id] = cur_page
    if cur_page < 0:
        cur_page = 0
        _user_mix_pages[user_id] = 0

    recent_tracks = await database.get_user_recent_tracks(
        user_id, limit=MIX_PAGE_SIZE, offset=cur_page * MIX_PAGE_SIZE
    )

    text = t["mix_menu_title"]

    if queue:
        items_str = "\n".join(f"{i+1}. 🎵 <b>{html.escape(item['title'])}</b>" for i, item in enumerate(queue))
        text += t["mix_current_queue"].format(count=len(queue), max_tracks=MAX_MIX_TRACKS, list=items_str)
    elif not recent_tracks and total_tracks == 0:
        text += "\n\n" + t["mix_empty_library"]

    keyboard_rows: list[list[InlineKeyboardButton]] = []

    # Кнопки для каждого трека с текущей страницы библиотеки пользователя
    queue_song_ids = {item["song_id"] for item in queue}
    for s_id, s_title in recent_tracks:
        is_selected = s_id in queue_song_ids
        mark = "✅" if is_selected else "➕"
        btn_text = f"{mark} {s_title[:24]}"
        keyboard_rows.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"mix_toggle:{s_id}")
        ])

    # Строка навигации по страницам библиотеки (если треков больше, чем MIX_PAGE_SIZE)
    if total_pages > 1:
        page_nav: list[InlineKeyboardButton] = []
        if cur_page > 0:
            page_nav.append(InlineKeyboardButton(text="◀️", callback_data="mix_page:prev"))
        page_nav.append(InlineKeyboardButton(text=f"📄 {cur_page + 1}/{total_pages} ({total_tracks})", callback_data="mix_page:noop"))
        if cur_page < total_pages - 1:
            page_nav.append(InlineKeyboardButton(text="▶️", callback_data="mix_page:next"))
        keyboard_rows.append(page_nav)

    # Кнопки управления
    ctrl_row: list[InlineKeyboardButton] = []
    if len(queue) >= 2:
        ctrl_row.append(InlineKeyboardButton(text=t["mix_btn_build"].format(count=len(queue)), callback_data="mix_build"))
    if queue:
        ctrl_row.append(InlineKeyboardButton(text=t["mix_btn_clear"], callback_data="mix_clear"))
    ctrl_row.append(InlineKeyboardButton(text=t["mix_btn_cancel"], callback_data="mix_cancel"))
    keyboard_rows.append(ctrl_row)

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


async def get_track_audio_bytes(song_id: str) -> tuple[bytes | None, str]:
    """Получает MP3 байты трека (из кэша Telegram или скачивает с Suno)."""
    title = "Suno Track"
    cached = await database.get_cached_track(song_id)
    if cached:
        fid, cached_title, _ = cached
        if cached_title:
            title = cached_title
        if fid:
            try:
                tg_file = await bot.get_file(fid)
                dest = io.BytesIO()
                await bot.download_file(tg_file.file_path, destination=dest)
                data = dest.getvalue()
                if is_valid_mp3(data):
                    return data, title
            except Exception as e:
                logger.warning("Не удалось скачать MP3 из кэша Telegram: %s", e)

    target_url = make_suno_url(song_id)

    session = HTTP_SESSION
    local_session = None
    if session is None or session.closed:
        local_session = aiohttp.ClientSession()
        session = local_session

    try:
        # Если в Telegram нет, пробуем прямое скачивание с Suno
        try:
            raw_audio, extracted_title, *rest = await download_direct_from_suno(target_url, session)
            if raw_audio and is_valid_mp3(raw_audio):
                return raw_audio, extracted_title or title
        except TrackNotFoundError:
            logger.info("Трек не найден на Suno (404): %s", target_url)
        except Exception as e:
            logger.warning("Ошибка прямого скачивания для микса (%s): %s", target_url, e)

        # Резервный способ через sunodownload.io
        try:
            fallback_raw, fb_title = await convert_and_download_mp3(target_url, session)
            if fallback_raw and is_valid_mp3(fallback_raw):
                return fallback_raw, fb_title or title
        except Exception as e:
            logger.warning("Ошибка резервного скачивания для микса (%s): %s", target_url, e)

        return None, title
    finally:
        if local_session and not local_session.closed:
            await local_session.close()


async def concatenate_tracks(
    audio_tracks: list[tuple[bytes, str]],
    mode: str = "normal",
) -> tuple[bytes | None, str]:
    """Склеивает аудиофайлы через ffmpeg.
    mode='normal': обычная последовательная склейка встык.
    mode='crossfade': плавный DJ-микс с наложением 3 секунды.
    Возвращает (mp3_bytes, tracklist_text)."""
    if len(audio_tracks) < 2:
        return None, ""

    loop = asyncio.get_running_loop()

    def _sync_ffmpeg():
        with tempfile.TemporaryDirectory() as td:
            input_files = []
            durations = []
            for i, (audio_bytes, _) in enumerate(audio_tracks):
                fp = os.path.join(td, f"track_{i}.mp3")
                with open(fp, "wb") as f:
                    f.write(audio_bytes)
                input_files.append(fp)
                try:
                    m = MP3(fp)
                    dur = m.info.length if m.info else 180.0
                except Exception:
                    dur = 180.0
                durations.append(dur)

            out_file = os.path.join(td, "mix.mp3")
            n = len(input_files)

            # Формируем треклист с точными таймкодами
            crossfade_dur = 3.0
            tracklist_lines = []
            cur_time = 0.0
            for i, (_, t_title) in enumerate(audio_tracks):
                mins = int(cur_time // 60)
                secs = int(cur_time % 60)
                tracklist_lines.append(f"<b>{mins:02d}:{secs:02d}</b> — {html.escape(t_title)}")
                if mode == "crossfade":
                    cur_time += max(0.0, durations[i] - crossfade_dur)
                else:
                    cur_time += durations[i]
            tracklist_text = "\n".join(tracklist_lines)

            # Динамический расчет безопасного битрейта: цель <= 47.5 МБ (лимит Telegram 50 МБ)
            total_dur = max(sum(durations), 60.0)
            target_bits = 47.5 * 1024 * 1024 * 8
            max_kbps = int(target_bits / total_dur) // 1000

            # Выбираем максимальный стандартный битрейт, укладывающийся в лимит
            standard_bitrates = [256, 192, 160, 128, 112, 96, 80, 64]
            chosen_b = 64
            for b in standard_bitrates:
                if b <= max_kbps:
                    chosen_b = b
                    break
            bitrate = f"{chosen_b}k"
            logger.info("Склейка микса (%s треков, ~%.1fs): расчетный max_kbps=%s, выбран битрейт=%s", n, total_dur, max_kbps, bitrate)

            cmd = ["ffmpeg", "-y"]
            for f in input_files:
                cmd.extend(["-i", f])

            if mode == "crossfade":
                filter_parts = []
                prev_label = "[0:a]"
                for i in range(1, n):
                    next_input = f"[{i}:a]"
                    out_label = "[out]" if i == n - 1 else f"[a{i}]"
                    filter_parts.append(f"{prev_label}{next_input}acrossfade=d={crossfade_dur}:c1=tri:c2=tri{out_label}")
                    prev_label = out_label
                filter_str = ";".join(filter_parts)
                cmd.extend([
                    "-filter_complex", filter_str,
                    "-map", "[out]",
                    "-c:a", "libmp3lame", "-b:a", bitrate,
                    out_file
                ])
            else:
                inputs_labels = "".join(f"[{i}:a]" for i in range(n))
                cmd.extend([
                    "-filter_complex", f"{inputs_labels}concat=n={n}:v=0:a=1[out]",
                    "-map", "[out]",
                    "-c:a", "libmp3lame", "-b:a", bitrate,
                    out_file
                ])

            import subprocess
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if proc.returncode == 0 and os.path.exists(out_file):
                # Гарантия размера: лимит Telegram Bot API 50 МБ (с запасом 48.5 МБ)
                max_allowed_bytes = int(48.5 * 1024 * 1024)
                if os.path.getsize(out_file) > max_allowed_bytes:
                    logger.warning("Микс получился %s байт (>48.5MB). Автоматически сжимаем...", os.path.getsize(out_file))
                    for lower_b in ["128k", "96k", "80k", "64k"]:
                        if os.path.getsize(out_file) <= max_allowed_bytes:
                            break
                        compressed_file = os.path.join(td, f"mix_{lower_b}.mp3")
                        cmp_cmd = [
                            "ffmpeg", "-y", "-i", out_file,
                            "-c:a", "libmp3lame", "-b:a", lower_b,
                            compressed_file
                        ]
                        cmp_proc = subprocess.run(cmp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        if cmp_proc.returncode == 0 and os.path.exists(compressed_file):
                            out_file = compressed_file
                            logger.info("Сжатие микса в %s: новый размер %s байт", lower_b, os.path.getsize(out_file))

                with open(out_file, "rb") as rf:
                    return rf.read(), tracklist_text
            else:
                logger.error("FFmpeg ошибка склейки: %s", proc.stderr.decode(errors="ignore"))
                return None, ""

    try:
        return await loop.run_in_executor(None, _sync_ffmpeg)
    except Exception as e:
        logger.error("Исключение при склейке треков: %s", e, exc_info=True)
        return None, ""


@dp.message(Command("mix"))
@dp.message(F.text.in_([t["btn_create_mix"] for t in TEXTS.values()]))
async def cmd_mix(message: types.Message):
    user_id = message.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(message.from_user))
    if not await check_user_subscription(user_id):
        t = TEXTS[lang]
        await message.answer(t["sub_required"], reply_markup=get_sub_keyboard(lang), parse_mode="HTML")
        return
    if user_id not in _user_mix_queues:
        _user_mix_queues[user_id] = []
    text, reply_markup = await render_mix_view(user_id, lang)
    await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


@dp.callback_query(F.data.startswith("mix_toggle:"))
async def handle_mix_toggle(callback: CallbackQuery):
    song_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]

    if user_id not in _user_mix_queues:
        _user_mix_queues[user_id] = []

    queue = _user_mix_queues[user_id]
    existing_idx = next((i for i, item in enumerate(queue) if item["song_id"] == song_id), None)

    if existing_idx is not None:
        queue.pop(existing_idx)
    else:
        if len(queue) >= MAX_MIX_TRACKS:
            await callback.answer(t["mix_max_reached"], show_alert=True)
            return
        # Находим название трека
        title = "Suno Track"
        cached = await database.get_cached_track(song_id)
        if cached and cached[1]:
            title = cached[1]
        else:
            user_tracks = await database.get_user_recent_tracks(user_id, limit=20)
            for s_id, s_title in user_tracks:
                if s_id == song_id:
                    title = s_title
                    break
        queue.append({"song_id": song_id, "title": title})

    await callback.answer()
    text, reply_markup = await render_mix_view(user_id, lang)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data == "mix_clear")
async def handle_mix_clear(callback: CallbackQuery):
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    _user_mix_queues[user_id] = []
    await callback.answer()
    text, reply_markup = await render_mix_view(user_id, lang)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data == "mix_cancel")
async def handle_mix_cancel(callback: CallbackQuery):
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    _user_mix_queues.pop(user_id, None)
    _user_mix_pages.pop(user_id, None)
    await callback.answer()
    try:
        await callback.message.edit_text(TEXTS[lang]["mix_cancelled"], parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("mix_page:"))
async def handle_mix_page(callback: CallbackQuery):
    action = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    if action == "noop":
        await callback.answer()
        return

    cur_page = _user_mix_pages.get(user_id, 0)
    if action == "prev":
        _user_mix_pages[user_id] = max(0, cur_page - 1)
    elif action == "next":
        _user_mix_pages[user_id] = cur_page + 1

    await callback.answer()
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    text, reply_markup = await render_mix_view(user_id, lang)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data == "mix_build")
async def handle_mix_build(callback: CallbackQuery):
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]
    queue = _user_mix_queues.get(user_id, [])

    if len(queue) < 2:
        await callback.answer(t["mix_min_tracks"], show_alert=True)
        return

    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["mix_mode_normal"], callback_data="mix_mode:normal")],
        [InlineKeyboardButton(text=t["mix_mode_crossfade"], callback_data="mix_mode:crossfade")],
        [InlineKeyboardButton(text=t["mix_btn_back"], callback_data="mix_back")],
    ])
    try:
        await callback.message.edit_text(t["mix_mode_prompt"], reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data == "mix_back")
async def handle_mix_back(callback: CallbackQuery):
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    await callback.answer()
    text, reply_markup = await render_mix_view(user_id, lang)
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data.startswith("mix_mode:"))
async def handle_mix_mode(callback: CallbackQuery):
    mode = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]
    queue = _user_mix_queues.get(user_id, [])

    if len(queue) < 2:
        await callback.answer(t["mix_min_tracks"], show_alert=True)
        return

    # ── Проверка лимитов на миксы ──────────────────────────────────────────────
    is_pro = await database.is_user_pro(user_id, admin_id=ADMIN_ID)
    allowed, current_mixes = await database.check_daily_limit(user_id, "mixes", FREE_DAILY_MIXES, is_pro)
    if not allowed:
        ref_url = f"https://t.me/sunosaver_bot?start=ref_{user_id}"
        await callback.answer(t["limit_mixes_alert"], show_alert=True)
        await callback.message.reply(
            t["limit_mixes_reached"].format(limit=FREE_DAILY_MIXES, ref_url=ref_url),
            parse_mode="HTML"
        )
        return

    if not is_pro and len(queue) > FREE_MAX_MIX_TRACKS:
        ref_url = f"https://t.me/sunosaver_bot?start=ref_{user_id}"
        await callback.answer(t["limit_mix_tracks_alert"], show_alert=True)
        await callback.message.reply(
            t["limit_mix_tracks_free"].format(free_max=FREE_MAX_MIX_TRACKS, ref_url=ref_url),
            parse_mode="HTML"
        )
        return

    await callback.answer()
    status_msg = await callback.message.reply(t["mix_processing"].format(count=len(queue)), parse_mode="HTML")

    try:
        # Скачиваем аудио всех треков
        audio_tracks: list[tuple[bytes, str]] = []
        for item in queue:
            try:
                raw_bytes, item_title = await get_track_audio_bytes(item["song_id"])
                if raw_bytes:
                    audio_tracks.append((raw_bytes, item_title or item["title"]))
                else:
                    logger.warning("Не удалось скачать трек %s для микса", item["song_id"])
            except Exception as e:
                logger.warning("Ошибка получения трека %s для микса: %s", item["song_id"], e)

        if len(audio_tracks) < 2:
            err_text = t["mix_error"] + t.get("error_contact", "")
            await status_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
            return

        # Склеиваем треки в ffmpeg
        mix_bytes, tracklist_text = await concatenate_tracks(audio_tracks, mode=mode)
        if not mix_bytes:
            err_text = t["mix_error"] + t.get("error_contact", "")
            await status_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
            return

        # Прошиваем теги ID3
        mode_label = t["mix_mode_crossfade"] if mode == "crossfade" else t["mix_mode_normal"]
        mix_title = f"Suno Mix ({len(audio_tracks)} tracks)"
        artist = "Suno AI (@sunosaver_bot)"
        tagged_mix, mix_duration = add_id3_tags(mix_bytes, mix_title, artist)

        track_count = len(audio_tracks)
        plural_word = "трека" if 2 <= track_count <= 4 else "треков"
        caption = (
            f"🎛 <b>Suno Mix ({track_count} {plural_word})</b>\n"
            f"🎧 {mode_label}\n\n"
            f"<b>Треклист:</b>\n{tracklist_text}\n\n"
            f"⚡️ @sunosaver_bot"
        )

        # Финальная проверка размера перед отправкой в Telegram (лимит 50 МБ)
        if len(tagged_mix) > 49 * 1024 * 1024:
            logger.warning("Микс после тегов превышает 49 МБ (%s байт). Пробуем дополнительно сжать...", len(tagged_mix))
            compressed_mix = await _convert_audio_to_mp3(tagged_mix, is_url=False)
            if compressed_mix and len(compressed_mix) <= 49 * 1024 * 1024:
                tagged_mix, mix_duration = add_id3_tags(compressed_mix, mix_title, artist)
            elif len(tagged_mix) > 50 * 1024 * 1024:
                logger.error("Размер микса (%s байт) превышает лимит Telegram Bot API (50 МБ)", len(tagged_mix))
                err_text = t.get("mix_too_large", t["mix_error"]) + t.get("error_contact", "")
                await status_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
                await notify_admin_error("handle_mix_mode:file_too_large", Exception(f"Микс {len(tagged_mix)} байт > 50 МБ"), f"tracks: {len(queue)}, mode: {mode}", user=callback.from_user)
                return

        audio_file = BufferedInputFile(tagged_mix, filename=f"Suno_Mix_{len(audio_tracks)}_tracks.mp3")
        await callback.message.reply_audio(
            audio=audio_file,
            caption=caption,
            title=mix_title,
            performer=artist,
            duration=mix_duration if mix_duration > 0 else None,
            request_timeout=180,
            parse_mode="HTML",
        )
        await database.increment_daily_usage(user_id, "mixes")

        _user_mix_queues.pop(user_id, None)
        _user_mix_pages.pop(user_id, None)
        await status_msg.delete()

    except TelegramEntityTooLarge as e:
        logger.error("TelegramEntityTooLarge при отправке микса: %s", e)
        await notify_admin_error("handle_mix_mode:entity_too_large", e, f"tracks: {len(queue)}, mode: {mode}", user=callback.from_user)
        try:
            err_text = t.get("mix_too_large", t["mix_error"]) + t.get("error_contact", "")
            await status_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
        except Exception:
            pass

    except Exception as e:
        logger.error("Ошибка при создании микса: %s", e, exc_info=True)
        if not isinstance(e, TrackNotFoundError):
            await notify_admin_error("handle_mix_mode", e, f"tracks: {len(queue)}, mode: {mode}", user=callback.from_user)
        try:
            err_text = t["mix_error"] + t.get("error_contact", "")
            await status_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
        except Exception:
            pass


@dp.callback_query(F.data.startswith("mix_quick:"))
async def handle_mix_quick(callback: CallbackQuery):
    raw_ids = callback.data.split(":", 1)[1].split(",")
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]

    _user_mix_queues[user_id] = []
    for s_id in raw_ids[:MAX_MIX_TRACKS]:
        title = "Suno Track"
        cached = await database.get_cached_track(s_id)
        if cached and cached[1]:
            title = cached[1]
        _user_mix_queues[user_id].append({"song_id": s_id, "title": title})

    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["mix_mode_normal"], callback_data="mix_mode:normal")],
        [InlineKeyboardButton(text=t["mix_mode_crossfade"], callback_data="mix_mode:crossfade")],
        [InlineKeyboardButton(text=t["mix_btn_cancel"], callback_data="mix_cancel")],
    ])
    await callback.message.reply(t["mix_mode_prompt"], reply_markup=kb, parse_mode="HTML")


# ─── Скачивание плейлиста и добавление в микс ──────────────────────────────────

@dp.callback_query(F.data.startswith("pl_dl:"))
async def handle_playlist_download(callback: CallbackQuery):
    playlist_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]

    if not await check_user_subscription(user_id):
        await callback.answer(t["sub_failed"], show_alert=True)
        return

    pl_data = await fetch_suno_playlist(playlist_id, HTTP_SESSION, limit=15)
    if not pl_data or not pl_data["tracks"]:
        await callback.answer(t["pl_not_found"], show_alert=True)
        return

    await callback.answer()
    tracks = pl_data["tracks"]
    await callback.message.answer(t["pl_downloading"].format(count=len(tracks)), parse_mode="HTML")

    for idx, tr in enumerate(tracks, 1):
        await _download_and_send(callback.message, tr["url"], lang, t, track_num=idx, total=len(tracks))


@dp.callback_query(F.data.startswith("pl_mix:"))
async def handle_playlist_mix(callback: CallbackQuery):
    playlist_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]

    if not await check_user_subscription(user_id):
        await callback.answer(t["sub_failed"], show_alert=True)
        return

    is_pro = await database.is_user_pro(user_id, admin_id=ADMIN_ID)
    max_tracks = MAX_MIX_TRACKS if is_pro else FREE_MAX_MIX_TRACKS

    pl_data = await fetch_suno_playlist(playlist_id, HTTP_SESSION, limit=max_tracks)
    if not pl_data or not pl_data["tracks"]:
        await callback.answer(t["pl_not_found"], show_alert=True)
        return

    await callback.answer()
    tracks = pl_data["tracks"]
    _user_mix_queues[user_id] = [{"song_id": tr["song_id"], "title": tr["title"]} for tr in tracks[:max_tracks]]

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["mix_mode_normal"], callback_data="mix_mode:normal")],
        [InlineKeyboardButton(text=t["mix_mode_crossfade"], callback_data="mix_mode:crossfade")],
        [InlineKeyboardButton(text=t["mix_btn_cancel"], callback_data="mix_cancel")],
    ])
    await callback.message.reply(
        f"{t['pl_mix_added'].format(count=len(_user_mix_queues[user_id]))}\n\n{t['mix_mode_prompt']}",
        reply_markup=kb,
        parse_mode="HTML"
    )


# ─── Скачивание WAV (WAV Callback) ─────────────────────────────────────────────

async def _pcm_to_wav(input_bytes: bytes) -> bytes | None:
    """Конвертирует аудиопоток (m4a/mp4/mp3) в PCM WAV через временный файл на диске
    (гарантирует корректный RIFF-заголовок и точную длительность).
    Автоматически подбирает частоту дискретизации (48kHz -> 44.1kHz -> 32kHz -> 24kHz -> 22.05kHz -> 16kHz),
    чтобы размер файла не превышал лимит Telegram Bot API (50 МБ)."""
    with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as in_f:
        in_f.write(input_bytes)
        in_path = in_f.name

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out_f:
        out_path = out_f.name

    try:
        rates = ["48000", "44100", "32000", "24000", "22050", "16000"]
        for rate in rates:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", in_path,
                "-vn", "-c:a", "pcm_s16le", "-ar", rate,
                out_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0 and os.path.exists(out_path):
                sz = os.path.getsize(out_path)
                if 1000 < sz <= 49 * 1024 * 1024:
                    with open(out_path, "rb") as rf:
                        return rf.read()
                elif sz > 49 * 1024 * 1024:
                    logger.info("WAV > 49MB (%s байт) при %s Hz, пробуем меньшую частоту дискретизации", sz, rate)
                    continue

        if os.path.exists(out_path):
            final_sz = os.path.getsize(out_path)
            if final_sz > 49 * 1024 * 1024:
                logger.warning("WAV > 50MB (%s байт) даже при 16kHz", final_sz)
                raise WavTooLargeError(f"WAV exceeds 50 MB limit: {final_sz} bytes")
            elif 1000 < final_sz <= 50 * 1024 * 1024:
                with open(out_path, "rb") as rf:
                    return rf.read()
        return None
    except WavTooLargeError:
        raise
    except Exception as e:
        logger.warning("Ошибка конвертации в WAV: %s", e)
        return None
    finally:
        if os.path.exists(in_path):
            try:
                os.remove(in_path)
            except Exception:
                pass
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except Exception:
                pass


async def download_direct_wav_from_suno(
    raw_id: str,
    session: aiohttp.ClientSession,
) -> tuple[bytes | None, str, str | None]:
    """Скачивает аудиопоток трека и конвертирует в студийный WAV.
    Возвращает (wav_bytes, title, resolved_uuid)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Origin": "https://suno.com",
        "Referer": "https://suno.com/",
    }
    uuid = raw_id
    try:
        # Если передан короткий ID или ссылка, извлекаем или разрешаем UUID
        m_uuid = UUID_PATTERN.search(raw_id)
        if m_uuid:
            uuid = m_uuid.group(0)
        else:
            try:
                async with session.get(
                    f"https://suno.com/s/{raw_id}", headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True, ssl=ssl_ctx,
                ) as r_suno:
                    final_url = str(r_suno.url)
                    found_uuid = extract_song_id(final_url)
                    if found_uuid and UUID_PATTERN.match(found_uuid):
                        uuid = found_uuid
                    else:
                        html_t = await r_suno.text(errors="ignore")
                        u_m = UUID_PATTERN.search(html_t)
                        if u_m:
                            uuid = u_m.group(0)
            except Exception as e:
                logger.warning("Не удалось разрешить короткий ID %s в UUID: %s", raw_id, e)

        # 1. Запрашиваем метаданные через studio-api для получения названия
        title = "Suno Track"
        if uuid and UUID_PATTERN.match(uuid):
            try:
                async with session.get(
                    f"https://studio-api.prod.suno.com/api/clip/{uuid}",
                    headers=headers, timeout=aiohttp.ClientTimeout(total=5), ssl=ssl_ctx,
                ) as meta_resp:
                    if meta_resp.status == 200:
                        data = await meta_resp.json()
                        title = data.get("title") or "Suno Track"
            except Exception:
                pass

        # 2. Основной метод: запрашиваем гостевые права studio-api и расшифровываем m4a
        if uuid and UUID_PATTERN.match(uuid):
            rights_url = "https://studio-api.prod.suno.com/api/mango/rights"
            rights_payload = {"content_params": {"content_id": uuid, "content_type": "clip"}}
            rights_headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Origin": "https://suno.com",
                "Referer": "https://suno.com/",
            }
            try:
                async with session.post(
                    rights_url, json=rights_payload, headers=rights_headers,
                    timeout=aiohttp.ClientTimeout(total=10), ssl=ssl_ctx,
                ) as r_resp:
                    if r_resp.status == 200:
                        rights = await r_resp.json()
                        glt = rights.get("glt")
                        wrapped_key_b64 = rights.get("key")
                        wrapped_iv_b64 = rights.get("iv")

                        if glt and wrapped_key_b64 and wrapped_iv_b64:
                            user_key = hashlib.sha256(glt.encode("utf-8")).digest()
                            wrapped_key = base64.b64decode(wrapped_key_b64)
                            wrapped_iv = base64.b64decode(wrapped_iv_b64)
                            aad = uuid.encode("utf-8")

                            def _decrypt_gcm(wrapped: bytes) -> bytes:
                                iv = wrapped[:12]
                                tag = wrapped[-16:]
                                ct = wrapped[12:-16]
                                d = Cipher(algorithms.AES(user_key), modes.GCM(iv, tag), backend=default_backend()).decryptor()
                                d.authenticate_additional_data(aad)
                                return d.update(ct) + d.finalize()

                            content_key = _decrypt_gcm(wrapped_key)
                            content_iv = _decrypt_gcm(wrapped_iv)

                            m4a_url = f"https://d2lwuy8qc234o3.cloudfront.net/1/clip/{uuid}.m4a"
                            async with session.get(
                                m4a_url, headers={"User-Agent": "Mozilla/5.0"},
                                timeout=aiohttp.ClientTimeout(total=30), ssl=ssl_ctx,
                            ) as stream_resp:
                                if stream_resp.status == 200:
                                    enc_bytes = await stream_resp.read()
                                    cipher = Cipher(algorithms.AES(content_key), modes.CTR(content_iv), backend=default_backend())
                                    dec = cipher.decryptor()
                                    dec_bytes = dec.update(enc_bytes) + dec.finalize()

                                    wav_out = await _pcm_to_wav(dec_bytes)
                                    if wav_out:
                                        logger.info("Конвертация в WAV успешна: %s байт", len(wav_out))
                                        return wav_out, title, uuid
            except Exception as e:
                logger.warning("Mango rights не удался для %s: %s", uuid, e)

        # 3. Резервный метод через Suno CDN (видео mp4 или mp3)
        if uuid:
            for fallback_url in [f"https://cdn1.suno.ai/{uuid}.mp4", f"https://cdn1.suno.ai/{uuid}.mp3"]:
                try:
                    async with session.get(
                        fallback_url, headers={"User-Agent": "Mozilla/5.0"},
                        timeout=aiohttp.ClientTimeout(total=30), ssl=ssl_ctx,
                    ) as f_resp:
                        if f_resp.status == 200:
                            f_bytes = await f_resp.read()
                            if len(f_bytes) > 50 * 1024:
                                wav_out = await _pcm_to_wav(f_bytes)
                                if wav_out:
                                    logger.info("Конвертация в WAV через CDN fallback (%s) успешна: %s байт", fallback_url, len(wav_out))
                                    return wav_out, title, uuid
                except Exception:
                    pass

    except WavTooLargeError:
        raise
    except Exception as e:
        logger.warning("Ошибка создания WAV: %s", e, exc_info=True)

    return None, "Suno Track", uuid


@dp.callback_query(F.data.startswith("wav:"))
async def handle_wav_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    song_id = callback.data.split(":", 1)[1]
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]

    if not await check_user_subscription(user_id):
        await callback.answer()
        await callback.message.reply(t["sub_required"], reply_markup=get_sub_keyboard(lang), parse_mode="HTML")
        return

    # ── Проверка дневного лимита WAV ──────────────────────────────────────────
    is_pro = await database.is_user_pro(user_id, admin_id=ADMIN_ID)
    allowed, _ = await database.check_daily_limit(user_id, "wav", FREE_DAILY_WAV, is_pro)
    if not allowed:
        ref_url = f"https://t.me/sunosaver_bot?start=ref_{user_id}"
        await callback.answer(t["limit_wav_alert"], show_alert=True)
        await callback.message.reply(
            t["limit_wav_reached"].format(limit=FREE_DAILY_WAV, ref_url=ref_url),
            parse_mode="HTML"
        )
        return

    # Проверяем кэш WAV
    cached_wav_fid = await database.get_cached_wav(song_id)
    cached_track = await database.get_cached_track(song_id)
    title = cached_track[1] if cached_track and cached_track[1] else "Suno Track"
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Suno Track"
    escaped_title = html.escape(safe_title)
    artist = "Suno AI (@sunosaver_bot)"
    caption = f"🎼 <b>{escaped_title} (WAV)</b>\n{t['artist_label']}: {artist}"

    if cached_wav_fid:
        await callback.answer()
        try:
            await callback.message.reply_audio(
                audio=cached_wav_fid,
                caption=caption,
                title=f"{safe_title} (WAV)",
                performer=artist,
                request_timeout=180,
                parse_mode="HTML",
            )
            await database.increment_daily_usage(user_id, "wav")
            return
        except Exception as e:
            logger.warning("Кэшированный WAV file_id устарел: %s", e)

    # Уведомляем пользователя о начале генерации
    await callback.answer(t["wav_generating"], show_alert=False)
    progress_msg = await callback.message.reply(f"⏳ {t['wav_generating']}", parse_mode="HTML")

    try:
        async with SEMAPHORE:
            wav_bytes, extracted_title, resolved_uuid = await download_direct_wav_from_suno(song_id, HTTP_SESSION)

        # Если прямое скачивание не удалось, проверяем кэш по resolved_uuid
        if not wav_bytes and resolved_uuid and resolved_uuid != song_id:
            uuid_cached_wav = await database.get_cached_wav(resolved_uuid)
            if uuid_cached_wav:
                await callback.message.reply_audio(
                    audio=uuid_cached_wav,
                    caption=caption,
                    title=f"{safe_title} (WAV)",
                    performer=artist,
                    request_timeout=180,
                    parse_mode="HTML",
                )
                await database.save_wav_cache(song_id, uuid_cached_wav)
                await progress_msg.delete()
                return

        # Если прямое скачивание всё ещё не вернуло байты, конвертируем из кэшированного в Telegram MP3
        if not wav_bytes and cached_track and cached_track[0]:
            try:
                logger.info("Конвертируем WAV из кэшированного Telegram MP3...")
                tg_file = await bot.get_file(cached_track[0])
                tg_bytes_io = await bot.download_file(tg_file.file_path)
                mp3_bytes = tg_bytes_io.read() if hasattr(tg_bytes_io, "read") else tg_bytes_io.getvalue()
                wav_bytes = await _pcm_to_wav(mp3_bytes)
            except Exception as e:
                logger.warning("Ошибка конвертации из Telegram MP3: %s", e)

        if not wav_bytes:
            err_text = t["wav_error"] + t.get("error_contact", "")
            await progress_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
            await notify_admin_error("handle_wav_callback:wav_failed", Exception("Не удалось сгенерировать WAV"), f"song_id: {song_id}", user=callback.from_user)
            return

        if extracted_title and extracted_title != "Suno Track":
            safe_title = re.sub(r'[\\/*?:"<>|]', "", extracted_title).strip() or safe_title
            escaped_title = html.escape(safe_title)
            caption = f"🎼 <b>{escaped_title} (WAV)</b>\n{t['artist_label']}: {artist}"

        wav_duration = None
        try:
            import wave
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                nframes = wf.getnframes()
                frate = wf.getframerate()
                if frate > 0:
                    wav_duration = int(round(nframes / float(frate)))
        except Exception:
            pass

        wav_file = BufferedInputFile(wav_bytes, filename=f"{safe_title}.wav")
        sent_msg = await callback.message.reply_audio(
            audio=wav_file,
            caption=caption,
            title=f"{safe_title} (WAV)",
            performer=artist,
            duration=wav_duration,
            request_timeout=180,
            parse_mode="HTML",
        )

        if sent_msg.audio:
            await database.save_wav_cache(song_id, sent_msg.audio.file_id)
            if resolved_uuid and resolved_uuid != song_id:
                await database.save_wav_cache(resolved_uuid, sent_msg.audio.file_id)
            await database.increment_daily_usage(user_id, "wav")

        await progress_msg.delete()

    except WavTooLargeError:
        try:
            err_text = t.get("wav_too_large", "⚠️ Этот трек слишком длинный для формата WAV (лимит Telegram — 50 МБ).\nРекомендуем слушать или скачивать трек в формате MP3.") + t.get("error_contact", "")
            await progress_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
        except Exception:
            pass
    except Exception as e:
        logger.error("Ошибка отправки WAV: %s", e, exc_info=True)
        await notify_admin_error("handle_wav_callback", e, f"song_id: {song_id}", user=callback.from_user)
        try:
            err_text = t["wav_error"] + t.get("error_contact", "")
            await progress_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
        except Exception:
            pass


async def generate_or_fetch_video(
    raw_id: str,
    session: aiohttp.ClientSession,
) -> tuple[str | bytes | None, str, str | None]:
    """
    Возвращает (video_data_or_path, title, resolved_uuid).
    video_data_or_path: путь к сгенерированному файлу .mp4, либо bytes скачанного видео, либо None.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Origin": "https://suno.com",
        "Referer": "https://suno.com/",
    }
    uuid = raw_id
    try:
        m_uuid = UUID_PATTERN.search(raw_id)
        if m_uuid:
            uuid = m_uuid.group(0)
        else:
            try:
                async with session.get(
                    f"https://suno.com/s/{raw_id}", headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True, ssl=ssl_ctx,
                ) as r_suno:
                    final_url = str(r_suno.url)
                    found_uuid = extract_song_id(final_url)
                    if found_uuid and UUID_PATTERN.match(found_uuid):
                        uuid = found_uuid
                    else:
                        html_t = await r_suno.text(errors="ignore")
                        u_m = UUID_PATTERN.search(html_t)
                        if u_m:
                            uuid = u_m.group(0)
            except Exception as e:
                logger.warning("Не удалось разрешить короткий ID %s в UUID: %s", raw_id, e)

        title = "Suno Track"
        # Проверяем кэш трека для названия
        cached_track = await database.get_cached_track(raw_id)
        if not cached_track and uuid:
            cached_track = await database.get_cached_track(uuid)
        if cached_track and cached_track[1]:
            title = cached_track[1]

        # Если названия нет, запрашиваем через studio-api clip
        if title == "Suno Track" and uuid and UUID_PATTERN.match(uuid):
            try:
                async with session.get(
                    f"https://studio-api.prod.suno.com/api/clip/{uuid}",
                    headers=headers, timeout=aiohttp.ClientTimeout(total=5), ssl=ssl_ctx,
                ) as meta_resp:
                    if meta_resp.status == 200:
                        data = await meta_resp.json()
                        title = data.get("title") or "Suno Track"
            except Exception:
                pass

        # 1. Попытка скачать готовый MP4 напрямую с Suno CDN
        if uuid and UUID_PATTERN.match(uuid):
            try:
                async with session.get(
                    f"https://cdn1.suno.ai/{uuid}.mp4",
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=15),
                    ssl=ssl_ctx,
                ) as v_resp:
                    if v_resp.status == 200:
                        mp4_bytes = await v_resp.read()
                        if len(mp4_bytes) > 200 * 1024:
                            logger.info("Скачан готовый MP4 с Suno CDN (%s байт) для %s", len(mp4_bytes), uuid)
                            return mp4_bytes, title, uuid
            except Exception:
                pass

        # 2. Получаем аудио (из кэша Telegram или скачиванием)
        audio_bytes = None
        if cached_track and cached_track[0]:
            try:
                tg_file = await bot.get_file(cached_track[0])
                tg_io = await bot.download_file(tg_file.file_path)
                audio_bytes = tg_io.read() if hasattr(tg_io, "read") else tg_io.getvalue()
                logger.info("Аудио получено из кэша Telegram (%s байт)", len(audio_bytes))
            except Exception as e:
                logger.warning("Не удалось скачать аудио из кэша Telegram: %s", e)

        if not audio_bytes and uuid:
            try:
                raw_audio, extracted_title, *rest = await download_direct_from_suno(make_suno_url(uuid), session)
                if raw_audio:
                    audio_bytes = raw_audio
                    if extracted_title and extracted_title != "Suno Track":
                        title = extracted_title
            except Exception as e:
                logger.warning("Не удалось скачать аудио для сборки видео (%s): %s", uuid, e)

        if not audio_bytes:
            logger.warning("Аудио не найдено для сборки видео: %s", raw_id)
            return None, title, uuid

        # 3. Скачиваем обложку трека
        cover_bytes = None
        if uuid and UUID_PATTERN.match(uuid):
            for img_url in [f"https://cdn1.suno.ai/image_large_{uuid}.jpeg", f"https://cdn1.suno.ai/image_{uuid}.jpeg"]:
                try:
                    async with session.get(
                        img_url, headers={"User-Agent": "Mozilla/5.0"},
                        timeout=aiohttp.ClientTimeout(total=10), ssl=ssl_ctx,
                    ) as img_resp:
                        if img_resp.status == 200:
                            img_data = await img_resp.read()
                            if len(img_data) > 5 * 1024:
                                cover_bytes = img_data
                                break
                except Exception:
                    pass

        # 4. Сборка видео MP4 через FFmpeg во временной папке
        tmp_dir = tempfile.mkdtemp(prefix="sunovideo_")
        audio_path = os.path.join(tmp_dir, "audio.mp3")
        cover_path = os.path.join(tmp_dir, "cover.jpg")
        output_path = os.path.join(tmp_dir, "output.mp4")

        with open(audio_path, "wb") as f:
            f.write(audio_bytes)

        if cover_bytes:
            with open(cover_path, "wb") as f:
                f.write(cover_bytes)
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-r", "2", "-i", cover_path,
                "-i", audio_path,
                "-c:v", "libx264", "-tune", "stillimage", "-preset", "ultrafast",
                "-c:a", "aac", "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-vf", "scale=720:720:force_original_aspect_ratio=decrease,pad=720:720:(ow-iw)/2:(oh-ih)/2:black",
                "-shortest", output_path
            ]
        else:
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=0x181824:s=720x720:r=2",
                "-i", audio_path,
                "-c:v", "libx264", "-tune", "stillimage", "-preset", "ultrafast",
                "-c:a", "aac", "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-shortest", output_path
            ]

        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
            if os.path.exists(cover_path):
                os.remove(cover_path)
        except Exception:
            pass

        if proc.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 100 * 1024:
            logger.info("MP4 видео успешно собрано (%s байт) для %s", os.path.getsize(output_path), raw_id)
            return output_path, title, uuid
        else:
            logger.error("Ошибка FFmpeg при сборке видео: %s", stderr.decode(errors="ignore"))
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
                os.rmdir(tmp_dir)
            except Exception:
                pass

    except Exception as e:
        logger.warning("Ошибка generate_or_fetch_video: %s", e, exc_info=True)

    return None, "Suno Track", uuid


@dp.callback_query(F.data.startswith("video:"))
async def handle_video_callback(callback: CallbackQuery):
    song_id = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    lang = await database.get_user_language(user_id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]

    if not await check_user_subscription(user_id):
        await callback.answer()
        await callback.message.reply(t["sub_required"], reply_markup=get_sub_keyboard(lang), parse_mode="HTML")
        return

    # 1. Проверяем кэш видео
    cached_video_fid = await database.get_cached_video(song_id)
    cached_track = await database.get_cached_track(song_id)
    title = cached_track[1] if cached_track and cached_track[1] else "Suno Track"
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or "Suno Track"
    escaped_title = html.escape(safe_title)
    caption = f"🎬 <b>{escaped_title}</b>\n⚡️ @sunosaver_bot"

    if cached_video_fid:
        await callback.answer()
        try:
            await callback.message.reply_video(
                video=cached_video_fid,
                caption=caption,
                supports_streaming=True,
                request_timeout=180,
                parse_mode="HTML",
            )
            return
        except Exception as e:
            logger.warning("Кэшированный video_file_id устарел: %s", e)

    # 2. Уведомление пользователя
    await callback.answer(t["video_generating"], show_alert=False)
    progress_msg = await callback.message.reply(f"⏳ {t['video_generating']}", parse_mode="HTML")

    try:
        async with SEMAPHORE:
            video_res, extracted_title, resolved_uuid = await generate_or_fetch_video(song_id, HTTP_SESSION)

        # Если прямое получение вернуло resolved_uuid, проверяем кэш по нему
        if not video_res and resolved_uuid and resolved_uuid != song_id:
            uuid_cached_video = await database.get_cached_video(resolved_uuid)
            if uuid_cached_video:
                await callback.message.reply_video(
                    video=uuid_cached_video,
                    caption=caption,
                    supports_streaming=True,
                    request_timeout=180,
                    parse_mode="HTML",
                )
                await database.save_video_cache(song_id, uuid_cached_video)
                await progress_msg.delete()
                return

        if not video_res:
            err_text = t["video_error"] + t.get("error_contact", "")
            await progress_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
            await notify_admin_error("handle_video_callback:video_failed", Exception("Не удалось собрать видео"), f"song_id: {song_id}", user=callback.from_user)
            return

        if extracted_title and extracted_title != "Suno Track":
            safe_title = re.sub(r'[\\/*?:"<>|]', "", extracted_title).strip() or safe_title
            escaped_title = html.escape(safe_title)
            caption = f"🎬 <b>{escaped_title}</b>\n⚡️ @sunosaver_bot"

        tmp_parent = None
        if isinstance(video_res, bytes):
            video_input = BufferedInputFile(video_res, filename=f"{safe_title}.mp4")
        else:
            tmp_parent = os.path.dirname(video_res)
            video_input = FSInputFile(video_res, filename=f"{safe_title}.mp4")

        sent_msg = await callback.message.reply_video(
            video=video_input,
            caption=caption,
            supports_streaming=True,
            width=720,
            height=720,
            request_timeout=180,
            parse_mode="HTML",
        )

        # Очищаем временный файл и папку на диске
        if isinstance(video_res, str) and os.path.exists(video_res):
            try:
                os.remove(video_res)
                if tmp_parent and os.path.exists(tmp_parent):
                    os.rmdir(tmp_parent)
            except Exception:
                pass

        if sent_msg.video:
            await database.save_video_cache(song_id, sent_msg.video.file_id)
            if resolved_uuid and resolved_uuid != song_id:
                await database.save_video_cache(resolved_uuid, sent_msg.video.file_id)

        await progress_msg.delete()

    except Exception as e:
        logger.error("Ошибка отправки видео: %s", e, exc_info=True)
        await notify_admin_error("handle_video_callback", e, f"song_id: {song_id}", user=callback.from_user)
        try:
            err_text = t["video_error"] + t.get("error_contact", "")
            await progress_msg.edit_text(err_text, reply_markup=get_support_keyboard(lang), parse_mode="HTML")
        except Exception:
            pass


# ─── Обработка Suno-ссылок ─────────────────────────────────────────────────────

@dp.message(F.text)
async def handle_suno_link(message: types.Message):
    suno_urls = find_all_suno_urls(message.text)
    if not suno_urls:
        return

    user_id = message.from_user.id
    lang    = await database.get_user_language(user_id, get_lang_fallback(message.from_user))
    t       = TEXTS[lang]

    # Блокировка
    if await database.is_user_banned(user_id):
        await message.answer(t["banned"], parse_mode="HTML")
        return

    # Обязательная подписка на канал
    if not await check_user_subscription(user_id):
        await message.answer(t["sub_required"], reply_markup=get_sub_keyboard(lang), parse_mode="HTML")
        return

    # Проверка ссылки на плейлист Suno
    playlist_id = extract_playlist_id(message.text)
    if playlist_id:
        status_msg = await message.answer(t["fetching"], parse_mode="HTML")
        pl_data = await fetch_suno_playlist(playlist_id, HTTP_SESSION)
        if not pl_data or not pl_data["tracks"]:
            await status_msg.edit_text(t["pl_not_found"], parse_mode="HTML")
            return

        text = (
            f"📂 <b>{t['pl_title']}:</b> <b>{html.escape(pl_data['title'])}</b>\n"
            f"👤 <b>{t['artist_label']}:</b> {html.escape(pl_data['creator'])}\n"
            f"🎵 <b>{t['pl_total_tracks']}:</b> {pl_data['total']} {t['pl_tracks_word']}\n\n"
            f"{t['pl_prompt_action']}"
        )
        kb = get_playlist_inline_keyboard(lang, playlist_id, len(pl_data["tracks"]))
        await status_msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
        return

    # Исключаем ссылки на плейлисты из списка обычных треков
    suno_urls = [u for u in suno_urls if not extract_playlist_id(u)]
    if not suno_urls:
        return

    # Если пользователь сейчас в режиме создания микса, добавляем треки в очередь микса
    if user_id in _user_mix_queues:
        queue = _user_mix_queues[user_id]
        added = 0
        for url in suno_urls:
            if len(queue) >= MAX_MIX_TRACKS:
                await message.answer(t["mix_max_reached"], parse_mode="HTML")
                break
            s_id = extract_song_id(url)
            if s_id and not any(it["song_id"] == s_id for it in queue):
                title = "Suno Track"
                cached = await database.get_cached_track(s_id)
                if cached and cached[1]:
                    title = cached[1]
                queue.append({"song_id": s_id, "title": title})
                added += 1

        if added > 0:
            text, reply_markup = await render_mix_view(user_id, lang)
            await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
            return

    # Rate limiting
    wait = check_rate_limit(user_id)
    if wait > 0:
        await message.answer(f"{t['error_rate_limit']} ({int(wait) + 1}s)", parse_mode="HTML")
        return

    # Уведомление при нескольких ссылках
    if len(suno_urls) > 1:
        await message.answer(
            t["multiple_links"].format(count=len(suno_urls)), parse_mode="HTML"
        )

    for idx, url in enumerate(suno_urls, 1):
        await _download_and_send(message, url, lang, t, track_num=idx, total=len(suno_urls))

    # Если было прислано 2 или 3 ссылки, предлагаем сразу склеить их в микс
    if len(suno_urls) >= 2:
        valid_ids = [extract_song_id(u) for u in suno_urls if extract_song_id(u)]
        if len(valid_ids) >= 2:
            quick_ids = ",".join(valid_ids)
            kb_mix = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=t["btn_mix_these"], callback_data=f"mix_quick:{quick_ids}")
            ]])
            await message.answer(f"🎛 <b>{t['btn_mix_these']}?</b>", reply_markup=kb_mix, parse_mode="HTML")


# ─── Регистрация команд ────────────────────────────────────────────────────────

async def setup_bot_commands():
    await bot.set_my_commands([
        BotCommand(command="start",    description="Restart bot"),
        BotCommand(command="artist",   description="👤 Set custom artist name"),
        BotCommand(command="pro",      description="⭐️ PRO status & referrals"),
        BotCommand(command="mix",      description="Create a music mix"),
        BotCommand(command="help",     description="How to download"),
        BotCommand(command="about",    description="About SunoSaver"),
        BotCommand(command="settings", description="Change language"),
    ], scope=BotCommandScopeDefault())
    await bot.set_my_commands([
        BotCommand(command="start",    description="Перезапустить бота"),
        BotCommand(command="artist",   description="👤 Настроить имя автора"),
        BotCommand(command="pro",      description="⭐️ PRO-статус и рефералка"),
        BotCommand(command="mix",      description="Собрать микс из песен"),
        BotCommand(command="help",     description="Инструкция"),
        BotCommand(command="about",    description="О сервисе"),
        BotCommand(command="settings", description="Сменить язык"),
    ], scope=BotCommandScopeDefault(), language_code="ru")
    await bot.set_my_commands([
        BotCommand(command="start",    description="Ботты қайта іске қосу"),
        BotCommand(command="artist",   description="👤 Автор есімін баптау"),
        BotCommand(command="pro",      description="⭐️ PRO-статус және достар"),
        BotCommand(command="mix",      description="Әндерден микс жасау"),
        BotCommand(command="help",     description="Нұсқаулық"),
        BotCommand(command="about",    description="Бот туралы"),
        BotCommand(command="settings", description="Тілді өзгерту"),
    ], scope=BotCommandScopeDefault(), language_code="kk")


# ─── Точка входа ───────────────────────────────────────────────────────────────

async def main():
    global SEMAPHORE, HTTP_SESSION

    # Создаём после запуска event loop (обязательно для Python 3.10+)
    SEMAPHORE    = asyncio.Semaphore(MAX_CONCURRENT)
    HTTP_SESSION = aiohttp.ClientSession()

    await database.init_db()

    # Очистка устаревшего кэша при старте
    deleted = await database.cleanup_old_cache(days=CACHE_TTL_DAYS)
    if deleted:
        logger.info("Очистка при старте: удалено %s устаревших записей", deleted)

    await setup_bot_commands()

    # Фоновая задача автоочистки каждые 24 ч
    asyncio.create_task(periodic_cache_cleanup())
    # Фоновая задача автобэкапа базы данных каждые 24 ч
    asyncio.create_task(periodic_db_backup())

    logger.info(
        "SunoSaver запущен! Admin: %s | Rate limit: %ss | Max links: %s",
        ADMIN_ID if ADMIN_ID else "не задан", RATE_LIMIT_SECONDS, MAX_LINKS_PER_MSG,
    )

    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        await HTTP_SESSION.close()
        logger.info("HTTP сессия закрыта.")


if __name__ == "__main__":
    asyncio.run(main())
