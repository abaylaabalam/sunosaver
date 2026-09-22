#!/usr/bin/env python3
"""
Скрипт рассылки об обновлении: Разделение на Вокал и Минус (Stems) + Донаты.
Запланирован на 11:00 по времени Алматы (06:00 UTC).
"""
import asyncio
import os
import sqlite3
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
        "🎙 <b>Грандиозное обновление в SunoSaver: Вокал и Минус!</b> 🎹✨\n\n"
        "Мы добавили самую ожидаемую и мощную функцию — <b>разделение песен на дорожки (Stems)</b> с помощью нейросети!\n\n"
        "<b>Что появилось:</b>\n"
        "🎙 <b>Чистый вокал (Акапелла)</b> — только чистый голос без музыки. Идеально для ремиксов, мэшапов, битов и сэмплов.\n"
        "🎹 <b>Минусовка (Караоке)</b> — чистая музыка без голоса. Отлично для караоке, живых выступлений и каверов.\n\n"
        "⚡️ <b>Как попробовать:</b>\n"
        "Просто отправьте боту ссылку на любую песню Suno и нажмите кнопку <b>[ 🎙 Вокал и Минус ]</b> под треком!\n\n"
        "───\n"
        "☕️ <b>Поддержка проекта:</b>\n"
        "Чтобы бот оставался быстрым и бесплатным, мы запустили меню поддержки. Если вам нравится сервис, вы можете поддержать оплату серверов:\n"
        "• Через <b>Telegram Stars</b> ⭐️ (любая сумма)\n"
        "• Картой <b>Kaspi</b> 🇰🇿\n"
        "• Через <b>Tribute</b> 💳 (карты РФ / Мир / TON)\n\n"
        "Лучшие спонсоры попадают в наш <b>🏆 Зал славы (Топ донаторов)</b>!\n"
        "Нажмите /donate или кнопку ниже 👇"
    ),
    "en": (
        "🎙 <b>Huge SunoSaver Update: Vocals & Instrumental!</b> 🎹✨\n\n"
        "We've launched the most anticipated AI feature — <b>Stem separation</b>!\n\n"
        "<b>What's new:</b>\n"
        "🎙 <b>Clean Vocals (Acapella)</b> — pure voice without music. Perfect for remixes, mashups, and sampling.\n"
        "🎹 <b>Instrumental (Karaoke)</b> — full backing track without voice. Great for karaoke, live performances, and covers.\n\n"
        "⚡️ <b>How to try:</b>\n"
        "Send any Suno song link to the bot and tap the <b>[ 🎙 Vocals & Instrumental ]</b> button under the track!\n\n"
        "───\n"
        "☕️ <b>Support the project:</b>\n"
        "To keep our servers fast and accessible, you can now support the bot:\n"
        "• Via <b>Telegram Stars</b> ⭐️\n"
        "• Via <b>Tribute / Card / TON</b> 💳\n"
        "• Enter our <b>🏆 Hall of Fame (Top Donators)</b>!\n\n"
        "Use /donate or tap the button below 👇"
    ),
    "kk": (
        "🎙 <b>SunoSaver-де үлкен жаңарту: Вокал және Минус!</b> 🎹✨\n\n"
        "Ботқа ең көп сұралған мүмкіндік — нейрожелі арқылы <b>әнді жеке дауыс пен минусқа бөлу</b> қосылды!\n\n"
        "<b>Жаңа мүмкіндіктер:</b>\n"
        "🎙 <b>Таза вокал (Акапелла)</b> — музыкасыз тек әншінің таза дауысы. Ремикстер мен сэмпл жасауға арналған.\n"
        "🎹 <b>Минусовка (Караоке)</b> — дауыссыз таза аспаптық музыка. Караоке айтуға, сахнаға шығуға және каверге таптырмас құрал.\n\n"
        "⚡️ <b>Қалай байқап көруге болады:</b>\n"
        "Ботқа кез келген Suno сілтемесін жіберіп, аудио астындағы <b>[ 🎙 Вокал пен Минус ]</b> батырмасын басыңыз!\n\n"
        "───\n"
        "☕️ <b>Ботты қолдау:</b>\n"
        "Бот жылдам жұмыс істеп, серверлерді ұстап тұру үшін қолдау көрсетуге болады:\n"
        "• <b>Telegram Stars</b> ⭐️\n"
        "• <b>Kaspi Gold</b> 🇰🇿\n"
        "• <b>Tribute / Карта</b> 💳\n\n"
        "Ең белсенді қолдаушылар <b>🏆 Донорлар тақтасына (Зал славы)</b> енеді!\n"
        "/donate пәрменін жіберіңіз немесе төмендегі батырманы басыңыз 👇"
    )
}

BUTTONS = {
    "ru": [
        [{"text": "🎙 Попробовать (Отправить трек)", "url": "https://t.me/sunosaver_bot"}],
        [{"text": "☕️ Поддержать бота / Донат", "url": "https://t.me/sunosaver_bot?start=donate"}],
    ],
    "en": [
        [{"text": "🎙 Try it now (Send track)", "url": "https://t.me/sunosaver_bot"}],
        [{"text": "☕️ Support the bot / Donate", "url": "https://t.me/sunosaver_bot?start=donate"}],
    ],
    "kk": [
        [{"text": "🎙 Қазір байқап көру", "url": "https://t.me/sunosaver_bot"}],
        [{"text": "☕️ Ботқа қолдау көрсету", "url": "https://t.me/sunosaver_bot?start=donate"}],
    ],
}


async def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not found!")
        return

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
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting broadcast for {total} users...")
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

            # Ограничение скорости: 25 сообщений в секунду
            await asyncio.sleep(0.04)

            if idx % 50 == 0 or idx == total:
                print(f"Progress: {idx}/{total} (sent: {sent}, blocked: {blocked}, failed: {failed})")

    duration = int(time.time() - start_time)
    summary = (
        f"📊 <b>Отчет о рассылке обновления (Вокал/Минус + Донаты):</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"✅ Успешно доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок отправки: <b>{failed}</b>\n"
        f"⏱ Время отправки: <b>{duration} сек</b>"
    )
    print(summary)

    # Отправляем итоговый отчет админу
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
