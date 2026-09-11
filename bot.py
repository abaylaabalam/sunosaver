import asyncio
import html
import io
import logging
import os
import re
import ssl
import time

import aiohttp
import certifi
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    BufferedInputFile,
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
from mutagen.id3 import ID3, TIT2, TPE1, COMM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

import database

load_dotenv()

# ─── Конфигурация ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в файле .env!")

CHANNEL_URL          = os.getenv("CHANNEL_URL", "https://t.me/youtubestantg")
MAX_CONCURRENT       = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", 5))
RATE_LIMIT_SECONDS   = int(os.getenv("RATE_LIMIT_SECONDS", 10))
MAX_LINKS_PER_MSG    = int(os.getenv("MAX_LINKS_PER_MESSAGE", 3))
ADMIN_ID             = int(os.getenv("ADMIN_ID", 0))   # 0 = не задан
CACHE_TTL_DAYS       = int(os.getenv("CACHE_TTL_DAYS", 30))

# ─── Логирование ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("SunoBot")

# ─── SSL ───────────────────────────────────────────────────────────────────────
ssl_ctx = ssl.create_default_context(cafile=certifi.where())

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
            "📖 <b>Как скачать песню из Suno:</b>\n\n"
            "1. Откройте трек на suno.com.\n"
            "2. Нажмите <b>«Поделиться»</b> → <b>«Скопировать ссылку»</b>.\n"
            "3. Отправьте ссылку боту — получите готовый MP3!\n\n"
            "💡 Поддерживаются ссылки вида:\n"
            "  • <code>suno.com/song/UUID</code>\n"
            "  • <code>suno.com/s/shortID</code>\n"
            "  • <code>share.suno.ai/...</code>\n\n"
            "📦 До 3 ссылок в одном сообщении."
        ),
        "about": (
            "ℹ️ <b>О сервисе Suno Saver:</b>\n\n"
            "• Быстрая конвертация и загрузка MP3\n"
            "• ID3-теги: название и исполнитель в файле\n"
            "• Кэширование — повторная отдача без скачивания\n"
            "• Резервный CDN-сервер при сбоях\n"
            "• Несколько ссылок в одном сообщении\n"
            "• Автоочистка устаревшего кэша"
        ),
        "settings":      "⚙️ <b>Настройки интерфейса</b>\n\nВыберите язык:",
        "lang_changed":  "✅ Язык переключен на <b>Русский</b>!",
        "channel_msg":   f"📢 <b>Наш официальный канал:</b>\nПодписывайтесь: {CHANNEL_URL}",
        "fetching":      "⏳ Обрабатываю ссылку...",
        "downloading":   "📥 Загружаю в Telegram...",
        "cdn_fallback":  "🔄 Основной сервис недоступен, пробую резервный CDN...",
        "error_download":"❌ Не удалось скачать трек. Убедитесь, что он публичный.",
        "error_telegram":"❌ Ошибка при отправке файла. Попробуйте позже.",
        "error_rate_limit": "⏳ Не так быстро! Подождите немного.",
        "banned":        "🚫 <b>Вы заблокированы</b> и не можете использовать бота.",
        "multiple_links":"🔗 Нашёл <b>{count}</b> ссылок. Скачиваю по очереди...",
        "artist_label":  "👤 Автор",
        "downloaded_via": "⚡️ <b>Скачано через:</b> @sunosaver_bot",
        "channel_sub_link": "📢 <b>Канал:</b>",
        "btn_lyrics":     "📜 Текст песни",
        "btn_wav":        "🎼 Скачать WAV",
        "lyrics_title":   "📜 <b>Текст песни «{title}»:</b>\n\n{lyrics}",
        "lyrics_none":    "ℹ️ У этого трека нет текста (инструментал).",
        "wav_generating": "⏳ Конвертирую и загружаю WAV (30-50 МБ)...",
        "wav_error":      "❌ Не удалось подготовить WAV файл. Попробуйте позже.",
        "btn_download_own": "🤖 Скачать свой трек",
        "btn_how_to":    "📥 Как скачать?",
        "btn_settings":  "⚙️ Настройки",
        "btn_about":     "ℹ️ О боте",
        "btn_channel":   "📢 Наш канал",
        "btn_go_channel":"➡️ Перейти в канал",
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
            "📖 <b>How to download a Suno song:</b>\n\n"
            "1. Open the track on suno.com.\n"
            "2. Click <b>«Share»</b> → <b>«Copy Link»</b>.\n"
            "3. Send the link here — get an MP3 back!\n\n"
            "💡 Supported link formats:\n"
            "  • <code>suno.com/song/UUID</code>\n"
            "  • <code>suno.com/s/shortID</code>\n"
            "  • <code>share.suno.ai/...</code>\n\n"
            "📦 Up to 3 links per message."
        ),
        "about": (
            "ℹ️ <b>About Suno Saver:</b>\n\n"
            "• Fast MP3 conversion and download\n"
            "• ID3 tags: title & artist embedded\n"
            "• Caching — instant re-delivery\n"
            "• Fallback CDN on failures\n"
            "• Multiple links in one message\n"
            "• Auto-cleanup of stale cache"
        ),
        "settings":      "⚙️ <b>Settings</b>\n\nChoose language:",
        "lang_changed":  "✅ Language changed to <b>English</b>!",
        "channel_msg":   f"📢 <b>Our Official Channel:</b>\nSubscribe: {CHANNEL_URL}",
        "fetching":      "⏳ Processing link...",
        "downloading":   "📥 Uploading to Telegram...",
        "cdn_fallback":  "🔄 Main service unavailable, trying fallback CDN...",
        "error_download":"❌ Could not download the track. Make sure it's public.",
        "error_telegram":"❌ Error delivering file. Please try again later.",
        "error_rate_limit": "⏳ Slow down! Please wait a moment.",
        "banned":        "🚫 <b>You are banned</b> and cannot use this bot.",
        "multiple_links":"🔗 Found <b>{count}</b> links. Downloading one by one...",
        "artist_label":  "👤 Artist",
        "downloaded_via": "⚡️ <b>Downloaded via:</b> @sunosaver_bot",
        "channel_sub_link": "📢 <b>Channel:</b>",
        "btn_lyrics":     "📜 Lyrics",
        "btn_wav":        "🎼 Download WAV",
        "lyrics_title":   "📜 <b>Lyrics for «{title}»:</b>\n\n{lyrics}",
        "lyrics_none":    "ℹ️ This track has no lyrics (instrumental).",
        "wav_generating": "⏳ Converting and uploading WAV (30-50 MB)...",
        "wav_error":      "❌ Could not prepare WAV file. Please try again later.",
        "btn_download_own": "🤖 Download Your Track",
        "btn_how_to":    "📥 How to download?",
        "btn_settings":  "⚙️ Settings",
        "btn_about":     "ℹ️ About",
        "btn_channel":   "📢 Our Channel",
        "btn_go_channel":"➡️ Go to Channel",
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


def extract_song_id(url: str) -> str | None:
    """UUID (song/...) или короткий ID (s/...) из ссылки Suno."""
    m = UUID_PATTERN.search(url)
    if m:
        return m.group(0)
    m = SHORT_ID_PATTERN.search(url)
    if m:
        return m.group(1)
    return None


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
    if user.language_code and user.language_code.startswith("ru"):
        return "ru"
    return "en"


def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def add_id3_tags(audio_bytes: bytes, title: str, artist: str) -> bytes:
    """Записывает ID3-теги в MP3. При ошибке возвращает исходные байты."""
    buf = io.BytesIO(audio_bytes)
    try:
        audio = MP3(buf, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TPE1(encoding=3, text=artist))
        audio.tags.add(COMM(encoding=3, lang="eng", desc="", text="Downloaded with @sunosaver_bot"))
        buf.seek(0)
        audio.save(buf)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.warning("ID3-теги не записаны: %s", e)
        return audio_bytes


# ─── Клавиатуры ────────────────────────────────────────────────────────────────

def get_track_inline_keyboard(
    lang: str,
    song_id: str | None = None,
    has_lyrics: bool = True,
) -> InlineKeyboardMarkup | None:
    if not song_id:
        return None
    t = TEXTS[lang]
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t["btn_lyrics"], callback_data=f"lyrics:{song_id}"),
        InlineKeyboardButton(text=t["btn_wav"], callback_data=f"wav:{song_id}"),
    ]])


def get_main_menu_keyboard(lang: str) -> ReplyKeyboardMarkup:
    t = TEXTS[lang]
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t["btn_how_to"]),  KeyboardButton(text=t["btn_settings"])],
            [KeyboardButton(text=t["btn_about"]),    KeyboardButton(text=t["btn_channel"])],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def get_language_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang:ru"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang:en"),
    ]])


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
        async with session.get(
            suno_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15),
            allow_redirects=True, ssl=ssl_ctx,
        ) as resp:
            final_url = str(resp.url)
            html_text = await resp.text(errors="ignore")

            # Извлекаем UUID песни
            uuid = extract_song_id(final_url)
            if not uuid or not UUID_PATTERN.match(uuid):
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

            # Если в HTML текст не найден, пробуем studio-api clip
            if not lyrics and uuid:
                try:
                    async with session.get(
                        f"https://studio-api.prod.suno.com/api/clip/{uuid}",
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=aiohttp.ClientTimeout(total=5), ssl=ssl_ctx,
                    ) as clip_resp:
                        if clip_resp.status == 200:
                            clip_data = await clip_resp.json()
                            lyrics = clip_data.get("metadata", {}).get("prompt")
                            if lyrics:
                                lyrics = lyrics.strip()
                except Exception:
                    pass

            # 1. Проверяем наличие видео MP4 на Suno CDN
            video_m = re.search(r'"video_url":"(https://[^"]+\.mp4)"', html_text)
            if video_m:
                mp4_url = video_m.group(1)
                logger.info("Прямое скачивание с Suno CDN через видео MP4: %s", mp4_url)
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-i", mp4_url,
                    "-vn", "-acodec", "libmp3lame", "-q:a", "2",
                    "-f", "mp3", "pipe:1",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0 and is_valid_mp3(stdout):
                    logger.info("Прямая конвертация через ffmpeg успешна: %s байт", len(stdout))
                    return stdout, title, lyrics, uuid

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

                        # Скачиваем m4a аудиопоток
                        m4a_url = f"https://d2lwuy8qc234o3.cloudfront.net/1/clip/{uuid}.m4a"
                        logger.info("Скачиваем аудиопоток Suno: %s", m4a_url)
                        async with session.get(
                            m4a_url, headers={"User-Agent": "Mozilla/5.0"},
                            timeout=aiohttp.ClientTimeout(total=30), ssl=ssl_ctx,
                        ) as stream_resp:
                            if stream_resp.status == 200:
                                enc_bytes = await stream_resp.read()
                                cipher = Cipher(algorithms.AES(content_key), modes.CTR(content_iv), backend=default_backend())
                                dec = cipher.decryptor()
                                dec_bytes = dec.update(enc_bytes) + dec.finalize()

                                # Конвертируем в MP3
                                proc = await asyncio.create_subprocess_exec(
                                    "ffmpeg", "-y", "-i", "pipe:0",
                                    "-vn", "-acodec", "libmp3lame", "-q:a", "2",
                                    "-f", "mp3", "pipe:1",
                                    stdin=asyncio.subprocess.PIPE,
                                    stdout=asyncio.subprocess.PIPE,
                                    stderr=asyncio.subprocess.PIPE,
                                )
                                mp3_out, mp3_err = await proc.communicate(input=dec_bytes)
                                if proc.returncode == 0 and is_valid_mp3(mp3_out):
                                    logger.info("Успешно расшифровано и конвертировано в MP3: %s байт", len(mp3_out))
                                    return mp3_out, title, lyrics, uuid
                                else:
                                    logger.warning("Ошибка ffmpeg при конвертации декодированного m4a")
                else:
                    logger.warning("studio-api rights вернул статус %s", r_resp.status)

    except Exception as e:
        logger.warning("Прямое скачивание Suno не удалось: %s", e, exc_info=True)

    return None, "Suno Track", None, uuid


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
    song_id = extract_song_id(suno_url)

    # ── Кэш ──────────────────────────────────────────────────────────────────
    if song_id:
        cache_data = await database.get_cached_track(song_id)
        if cache_data:
            cached_fid, cached_title, cached_lyrics = cache_data
            safe_title = cached_title or "Suno Track"
            escaped_title = html.escape(safe_title)
            artist = "Suno AI (@sunosaver_bot)"
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
                return True
            except Exception as e:
                logger.warning("Кэшированный file_id устарел, перекачиваем: %s", e)

    # ── Статусное сообщение ───────────────────────────────────────────────────
    prefix     = f"[{track_num}/{total}] " if total and total > 1 else ""
    status_msg = await message.answer(f"{prefix}{t['fetching']}", parse_mode="HTML")
    delete_status = False

    try:
        # ── 1. Прямая загрузка с Suno через ffmpeg (основной метод) ───────────
        lyrics = None
        resolved_uuid = None
        async with SEMAPHORE:
            raw_audio, title, lyrics, resolved_uuid = await download_direct_from_suno(suno_url, HTTP_SESSION)

        # ── 2. Резерв через sunodownload.io если прямой метод не сработал ──────
        if not raw_audio:
            logger.info("Пробуем резервный метод через sunodownload.io")
            async with SEMAPHORE:
                raw_audio, title = await convert_and_download_mp3(suno_url, HTTP_SESSION)

        if not raw_audio:
            await status_msg.edit_text(t["error_download"], parse_mode="HTML")
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
        artist         = "Suno AI (@sunosaver_bot)"
        tagged_audio   = add_id3_tags(raw_audio, safe_title, artist)
        audio_file     = BufferedInputFile(tagged_audio, filename=f"{safe_title}.mp3")
        caption        = f"🎵 <b>{escaped_title}</b>\n{t['artist_label']}: {artist}"
        reply_markup   = get_track_inline_keyboard(lang, song_id)

        # ── Отправка ──────────────────────────────────────────────────────────
        sent_msg = await message.answer_audio(
            audio=audio_file, caption=caption,
            title=safe_title, performer=artist,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

        if sent_msg.audio:
            if song_id:
                await database.save_track_cache(song_id, sent_msg.audio.file_id, safe_title, lyrics)
            if original_song_id and original_song_id != song_id:
                await database.save_track_cache(original_song_id, sent_msg.audio.file_id, safe_title, lyrics)

        delete_status = True
        return True

    except Exception as e:
        logger.error("Ошибка пайплайна: %s", e, exc_info=True)
        try:
            await status_msg.edit_text(t["error_telegram"], parse_mode="HTML")
        except Exception:
            await message.answer(t["error_telegram"], parse_mode="HTML")
        return False

    finally:
        if delete_status:
            try:
                await status_msg.delete()
            except Exception:
                pass


# ─── Команды ───────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["start"], reply_markup=get_main_menu_keyboard(lang), parse_mode="HTML")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["help"], parse_mode="HTML")


@dp.message(Command("settings"))
async def cmd_settings(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["settings"], reply_markup=get_language_inline_keyboard(), parse_mode="HTML")


# ─── Команды администратора ────────────────────────────────────────────────────

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    s = await database.get_stats()
    await message.answer(
        f"📊 <b>Статистика SunoSaver</b>\n\n"
        f"👥 Пользователей:   <b>{s['total_users']:,}</b>\n"
        f"🎵 Треков скачано:  <b>{s['total_downloads']:,}</b>\n"
        f"💾 В кэше:          <b>{s['cached_tracks']:,}</b>\n"
        f"🚫 Заблокировано:   <b>{s['banned_users']:,}</b>",
        parse_mode="HTML",
    )


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


# ─── Кнопки меню ───────────────────────────────────────────────────────────────

@dp.message(F.text.in_(["📥 Как скачать?", "📥 How to download?"]))
async def btn_help(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["help"], parse_mode="HTML")


@dp.message(F.text.in_(["⚙️ Настройки", "⚙️ Settings"]))
async def btn_settings(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["settings"], reply_markup=get_language_inline_keyboard(), parse_mode="HTML")


@dp.message(F.text.in_(["ℹ️ О боте", "ℹ️ About"]))
async def btn_about(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    await message.answer(TEXTS[lang]["about"], parse_mode="HTML")


@dp.message(F.text.in_(["📢 Наш канал", "📢 Our Channel"]))
async def btn_channel(message: types.Message):
    lang = await database.get_user_language(message.from_user.id, get_lang_fallback(message.from_user))
    t  = TEXTS[lang]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t["btn_go_channel"], url=CHANNEL_URL)
    ]])
    await message.answer(t["channel_msg"], reply_markup=kb, parse_mode="HTML")


# ─── Смена языка ───────────────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("set_lang:"))
async def handle_language_selection(callback: CallbackQuery):
    lang_map = {"set_lang:ru": "ru", "set_lang:en": "en"}
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
    msg_text = f"📜 <b>Текст песни «{escaped_title}»:</b>\n\n<blockquote>{escaped_lyrics}</blockquote>" if lang == "ru" else f"📜 <b>Lyrics for «{escaped_title}»:</b>\n\n<blockquote>{escaped_lyrics}</blockquote>"

    if len(msg_text) > 4000:
        msg_text = msg_text[:3950] + "...\n</blockquote>"

    await callback.message.reply(msg_text, parse_mode="HTML")


# ─── Скачивание WAV (WAV Callback) ─────────────────────────────────────────────

async def _pcm_to_wav(input_bytes: bytes) -> bytes | None:
    """Конвертирует аудиопоток (m4a/mp4/mp3) в PCM WAV.
    Автоматически подбирает частоту дискретизации (48kHz -> 44.1kHz -> 32kHz),
    чтобы размер файла не превышал лимит Telegram Bot API (50 МБ)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", "pipe:0",
            "-vn", "-c:a", "pcm_s16le", "-ar", "48000",
            "-f", "wav", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        wav_out, _ = await proc.communicate(input=input_bytes)
        if proc.returncode != 0 or len(wav_out) < 1000:
            return None

        # Лимит 49 МБ для запаса (Telegram Bot API максимум 50 МБ)
        if len(wav_out) > 49 * 1024 * 1024:
            logger.info("WAV > 49MB (%s байт), пробуем 44100 Hz", len(wav_out))
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", "pipe:0",
                "-vn", "-c:a", "pcm_s16le", "-ar", "44100",
                "-f", "wav", "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            wav_44k, _ = await proc.communicate(input=input_bytes)
            if proc.returncode == 0 and len(wav_44k) > 1000:
                wav_out = wav_44k

        if len(wav_out) > 49 * 1024 * 1024:
            logger.info("WAV всё ещё > 49MB (%s байт), пробуем 32000 Hz", len(wav_out))
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", "pipe:0",
                "-vn", "-c:a", "pcm_s16le", "-ar", "32000",
                "-f", "wav", "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            wav_32k, _ = await proc.communicate(input=input_bytes)
            if proc.returncode == 0 and len(wav_32k) > 1000:
                wav_out = wav_32k

        return wav_out if len(wav_out) <= 50 * 1024 * 1024 else None
    except Exception as e:
        logger.warning("Ошибка конвертации в WAV: %s", e)
        return None


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

    except Exception as e:
        logger.warning("Ошибка создания WAV: %s", e, exc_info=True)

    return None, "Suno Track", uuid


@dp.callback_query(F.data.startswith("wav:"))
async def handle_wav_callback(callback: CallbackQuery):
    song_id = callback.data.split(":", 1)[1]
    lang = await database.get_user_language(callback.from_user.id, get_lang_fallback(callback.from_user))
    t = TEXTS[lang]

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
            await progress_msg.edit_text(t["wav_error"], parse_mode="HTML")
            return

        if extracted_title and extracted_title != "Suno Track":
            safe_title = re.sub(r'[\\/*?:"<>|]', "", extracted_title).strip() or safe_title
            escaped_title = html.escape(safe_title)
            caption = f"🎼 <b>{escaped_title} (WAV)</b>\n{t['artist_label']}: {artist}"

        wav_file = BufferedInputFile(wav_bytes, filename=f"{safe_title}.wav")
        sent_msg = await callback.message.reply_audio(
            audio=wav_file,
            caption=caption,
            title=f"{safe_title} (WAV)",
            performer=artist,
            request_timeout=180,
            parse_mode="HTML",
        )

        if sent_msg.audio:
            await database.save_wav_cache(song_id, sent_msg.audio.file_id)
            if resolved_uuid and resolved_uuid != song_id:
                await database.save_wav_cache(resolved_uuid, sent_msg.audio.file_id)

        await progress_msg.delete()

    except Exception as e:
        logger.error("Ошибка отправки WAV: %s", e, exc_info=True)
        try:
            await progress_msg.edit_text(t["wav_error"], parse_mode="HTML")
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


# ─── Регистрация команд ────────────────────────────────────────────────────────

async def setup_bot_commands():
    await bot.set_my_commands([
        BotCommand(command="start",    description="Restart bot"),
        BotCommand(command="help",     description="How to download"),
        BotCommand(command="settings", description="Change language"),
    ], scope=BotCommandScopeDefault())
    await bot.set_my_commands([
        BotCommand(command="start",    description="Перезапустить бота"),
        BotCommand(command="help",     description="Инструкция"),
        BotCommand(command="settings", description="Сменить язык"),
    ], scope=BotCommandScopeDefault(), language_code="ru")


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
