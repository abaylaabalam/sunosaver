#!/usr/bin/env python3
"""
Скрипт рассылки Сообщения 1: Обновление (ZIP-архивы плейлистов + Инлайн-поиск).
Поддерживает языки RU, KK, EN.
Поддерживает флаг --test-admin для предварительной проверки на админе.
"""
import asyncio
import os
import sqlite3
import sys
import time
import aiohttp
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 1062368779))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db")

TEXTS = {
    "ru": (
        "⚡️ <b>Большое обновление SunoSaver!</b>\n\n"
        "Мы добавили две функции, которые сделают работу с музыкой ещё быстрее и удобнее:\n\n"
        "📦 <b>1. Скачивание плейлистов в ZIP-архив</b>\n"
        "Теперь не нужно скачивать треки по одному! Отправьте боту ссылку на плейлист <code>suno.com/playlist/...</code> и нажмите <b>«📦 Скачать ZIP-архивом»</b>. Все треки аккуратно упакуются в один архив с правильными названиями и обложками.\n\n"
        "🔍 <b>2. Инлайн-поиск в любых чатах</b>\n"
        "Поделиться любимым треком с друзьями стало проще простого. В <b>любом чате</b> или группе напишите:\n"
        "<code>@sunosaver_bot название песни</code>\n"
        "и выберите нужный трек из результатов — он мгновенно отправится в чат!\n\n"
        "🎧 Попробуйте прямо сейчас: отправьте плейлист или протестируйте инлайн-поиск!"
    ),
    "kk": (
        "⚡️ <b>SunoSaver-де үлкен жаңарту!</b>\n\n"
        "Сіздер үшін музыкамен жұмыс істеуді одан да ыңғайлы ететін 2 жаңа мүмкіндік қостық:\n\n"
        "📦 <b>1. Плейлистерді ZIP-архивпен жүктеу</b>\n"
        "Енді әр әнді жеке-жеке жүктеудің қажеті жоқ! Ботқа <code>suno.com/playlist/...</code> сілтемесін жіберіп, <b>«📦 ZIP-архив ретінде жүктеу»</b> батырмасын басыңыз. Барлық тректер мұқабасымен және атауларымен бір файлға жинақталып жіберіледі.\n\n"
        "🔍 <b>2. Кез келген чатта инлайн-іздеу</b>\n"
        "Достарыңызбен тректерді бөлісу оңай болды. <b>Кез келген чатта</b> немесе топта былай жазыңыз:\n"
        "<code>@sunosaver_bot ән атауы</code>\n"
        "сөйтіп шыққан тізімнен керегін таңдаңыз — трек бірден чатқа жіберіледі!\n\n"
        "🎧 Дәл қазір тексеріп көріңіз: плейлист сілтемесін жіберіңіз немесе іздеуді сынап көріңіз!"
    ),
    "en": (
        "⚡️ <b>Major SunoSaver Update!</b>\n\n"
        "We've rolled out two brand new features to make your music experience even smoother:\n\n"
        "📦 <b>1. Download Full Playlists as a ZIP Archive</b>\n"
        "No need to download tracks one by one anymore! Send any <code>suno.com/playlist/...</code> link to the bot and tap <b>\"📦 Download as ZIP\"</b>. All songs will be packed into a neat archive with proper tags and cover art.\n\n"
        "🔍 <b>2. Inline Music Search in Any Chat</b>\n"
        "Share your favorite generated tracks instantly. In <b>any Telegram chat</b> or group, type:\n"
        "<code>@sunosaver_bot song name</code>\n"
        "and tap on any track to share it with your friends right away!\n\n"
        "🎧 Try it right now — paste a playlist link or test the inline search!"
    ),
}

BUTTONS = {
    "ru": [
        [{"text": "🔍 Попробовать инлайн-поиск", "switch_inline_query": ""}],
        [{"text": "🤖 Открыть бота", "url": "https://t.me/sunosaver_bot"}],
    ],
    "kk": [
        [{"text": "🔍 Инлайн-іздеуді көру", "switch_inline_query": ""}],
        [{"text": "🤖 Ботты ашу", "url": "https://t.me/sunosaver_bot"}],
    ],
    "en": [
        [{"text": "🔍 Try Inline Search", "switch_inline_query": ""}],
        [{"text": "🤖 Open Bot", "url": "https://t.me/sunosaver_bot"}],
    ],
}


async def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not found!")
        return

    is_test = "--test-admin" in sys.argv

    if is_test:
        users = [(ADMIN_ID, "ru")]
        print(f"TEST MODE: Sending only to admin {ADMIN_ID}...")
    else:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT u.user_id, COALESCE(u.language, 'ru')
            FROM users u
            LEFT JOIN banned_users b ON u.user_id = b.user_id
            WHERE b.user_id IS NULL
        """)
        users = c.fetchall()
        conn.close()

    total = len(users)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting news broadcast for {total} users...")
    start_time = time.time()
    sent = 0
    blocked = 0
    failed = 0

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    async with aiohttp.ClientSession() as session:
        for idx, (uid, lang) in enumerate(users, 1):
            text = TEXTS.get(lang, TEXTS["ru"])
            kb = BUTTONS.get(lang, BUTTONS["ru"])
            payload = {
                "chat_id": uid,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": kb},
            }

            for attempt in range(3):
                try:
                    async with session.post(api_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        res_json = await resp.json()
                        if resp.status == 200 and res_json.get("ok"):
                            sent += 1
                            break
                        elif resp.status == 403:
                            blocked += 1
                            break
                        elif resp.status == 429:
                            retry_after = res_json.get("parameters", {}).get("retry_after", 3)
                            print(f"Rate limited. Waiting {retry_after}s...")
                            await asyncio.sleep(retry_after)
                            continue
                        else:
                            print(f"Failed user {uid}: {res_json.get('description')}")
                            failed += 1
                            break
                except Exception as e:
                    if attempt == 2:
                        failed += 1
                    await asyncio.sleep(1)

            # Ограничение скорости: ~25 сообщений в секунду
            await asyncio.sleep(0.04)

            if idx % 50 == 0 or idx == total:
                print(f"Progress: {idx}/{total} (sent: {sent}, blocked: {blocked}, failed: {failed})")

    duration = int(time.time() - start_time)
    summary = (
        f"📊 <b>Отчет о рассылке обновления (ZIP + Инлайн-поиск):</b>\n\n"
        f"👥 Всего получателей: <b>{total}</b>\n"
        f"✅ Успешно доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок: <b>{failed}</b>\n"
        f"⏱ Время: <b>{duration} сек</b>"
    )
    print(summary)

    if not is_test:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    api_url,
                    json={"chat_id": ADMIN_ID, "text": summary, "parse_mode": "HTML"},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
        except Exception as e:
            print(f"Failed to notify admin: {e}")


if __name__ == "__main__":
    asyncio.run(main())
