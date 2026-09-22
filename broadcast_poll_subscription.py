#!/usr/bin/env python3
"""
Скрипт рассылки Сообщения 2: Опрос о микро-подписке ($1–$2) и поддержании серверов.
Запланирован на 18:00 (13:00 UTC).
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
        "❤️ <b>Честный разговор о будущем SunoSaver</b>\n\n"
        "Дорогие друзья, нашему боту уже доверяют тысячи пользователей, а через наши серверы прошло уже более <b>25 000 треков</b>. Мы каждый день делаем всё, чтобы SunoSaver работал быстро, без сбоев и скачивал музыку в максимальном качестве.\n\n"
        "🥺 <b>Но держать проект бесплатным становится всё тяжелее.</b>\n"
        "Обработка аудио, конвертация WAV/видео, разделение вокала и хранение терабайтов кэша требуют мощных серверов. До сих пор мы оплачивали все расходы из собственного кармана и на чистом энтузиазме. Но с каждым днем нагрузка растет, и расходы на сервера подходят к критической точке.\n\n"
        "Мы <b>категорически не хотим</b> вешать назойливую рекламу казино и сомнительных каналов, которая испортит бота.\n\n"
        "💡 <b>Мы думаем о символической подписке:</b>\n"
        "Всего <b>$1 – $2 в месяц</b> (или ~50–100 Telegram Stars / чашка кофе раз в месяц), которая даст безлимит, приоритетную скорость и позволит боту жить и развиваться дальше.\n\n"
        "Нам невероятно важно ваше мнение. Пожалуйста, проголосуйте честно:\n\n"
        "👇 <b>Готовы ли вы поддержать бота такой подпиской?</b>"
    ),
    "kk": (
        "❤️ <b>SunoSaver-дің болашағы туралы ашық әңгіме</b>\n\n"
        "Құрметті достар, біздің ботқа мыңдаған қолданушылар сенім білдіріп, қазірдің өзінде <b>25 000-нан астам трек</b> жүктелді. Біз SunoSaver-дің үздіксіз, жылдам әрі жоғары сапада жұмыс істеуі үшін бар күшімізді салып келеміз.\n\n"
        "🥺 <b>Бірақ ботты толықтай тегін ұстап тұру барған сайын қиындап барады.</b>\n"
        "Аудиоларды өңдеу, WAV/бейне түрлендіру, вокалды бөлу және терабайттаған кэшті сақтау қуатты серверлерді талап етеді. Осы уақытқа дейін серверлердің барлық шығынын өз қалтамыздан төлеп келдік. Алайда қолданушылар көбейген сайын сервер шығындары да қатты өсуде.\n\n"
        "Біз ботқа спам, казино немесе күмәнді жарнамаларды <b>мүлдем қосқымыз келмейді</b>.\n\n"
        "💡 <b>Біз символдық шағын жазылым жасауды ойластырып отырмыз:</b>\n"
        "Бар болғаны айына <b>$1 – $2</b> (немесе ~50–100 Telegram Stars / айына 1 кесе кофе құны), бұл шектеусіз жүктеу, жоғары жылдамдық беріп, боттың жабылып қалмауына көмектеседі.\n\n"
        "Біз үшін сіздің пікіріңіз өте маңызды. Шын көңілмен дауыс беруіңізді сұраймыз:\n\n"
        "👇 <b>Ботты осындай жазылыммен қолдауға дайынсыз ба?</b>"
    ),
    "en": (
        "❤️ <b>An honest word about SunoSaver’s future</b>\n\n"
        "Dear friends, thousands of creators now rely on SunoSaver, with over <b>25,000 tracks</b> downloaded through our bot. We put our heart into keeping it lightning-fast and reliable every single day.\n\n"
        "🥺 <b>However, keeping the service completely free has become really difficult.</b>\n"
        "Heavy audio processing, WAV & video generation, stem separation, and caching gigabytes of music require powerful dedicated servers. Up until now, we’ve covered all server bills completely out-of-pocket. But as traffic surges, hosting costs are reaching a breaking point.\n\n"
        "We <b>refuse</b> to clutter your screen with intrusive spam or shady ads.\n\n"
        "💡 <b>We are considering a micro-subscription:</b>\n"
        "Just <b>$1 – $2 per month</b> (or ~50–100 Telegram Stars / the price of half a coffee) for unlimited access, top download speeds, and keeping the servers running smoothly.\n\n"
        "Your voice means everything to us. Please cast your honest vote below:\n\n"
        "👇 <b>Would you support SunoSaver with a $1–$2/mo micro-subscription?</b>"
    ),
}

BUTTONS = {
    "ru": [
        [{"text": "👍 Да, готов поддержать ($1-2)", "callback_data": "poll:sub:yes"}],
        [{"text": "👎 Нет, лучше оставьте бесплатно", "callback_data": "poll:sub:no"}],
    ],
    "kk": [
        [{"text": "👍 Иә, қолдауға дайынмын ($1-2)", "callback_data": "poll:sub:yes"}],
        [{"text": "👎 Жоқ, тегін қалғаны дұрыс", "callback_data": "poll:sub:no"}],
    ],
    "en": [
        [{"text": "👍 Yes, I'd support ($1-2)", "callback_data": "poll:sub:yes"}],
        [{"text": "👎 No, keep it completely free", "callback_data": "poll:sub:no"}],
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
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting poll broadcast for {total} users...")
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
        f"📊 <b>Отчет о рассылке опроса (Микро-подписка $1–$2):</b>\n\n"
        f"👥 Всего получателей: <b>{total}</b>\n"
        f"✅ Успешно доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок: <b>{failed}</b>\n"
        f"⏱ Время: <b>{duration} сек</b>\n\n"
        f"💡 <i>Следить за результатами голосов можно командой /poll_stats в боте.</i>"
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
