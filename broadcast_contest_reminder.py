#!/usr/bin/env python3
"""
Скрипт рассылки-напоминания о конкурсе (20 вечных PRO-аккаунтов).
Запланирован на 12:00 по времени Алматы/Астаны (07:00 UTC).
Поддерживает флаги:
  --dry-run      : Проверка базы данных и подсчет пользователей без реальной отправки.
  --test-admin   : Отправка тестового сообщения только администратору для проверки форматирования.
"""
import asyncio
import os
import sys
import sqlite3
import time
import aiohttp
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 1062368779))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db")


def get_participant_count() -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM contest_participants WHERE contest_id = '1000users'")
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        print(f"Error fetching participant count: {e}")
        return 0


def get_texts(participant_count: int) -> dict[str, str]:
    count_str = f"{participant_count}" if participant_count > 0 else "100+"
    return {
        "ru": (
            "🔥 <b>НАПОМИНАНИЕ: РОЗЫГРЫШ 20 ВЕЧНЫХ PRO-АККАУНТОВ!</b> 🎁\n\n"
            f"Уже <b>{count_str} участников</b> зарегистрировались в юбилейном конкурсе в честь 1 000 пользователей!\n\n"
            "Если вы ещё не нажали кнопку участия — не упустите шанс получить <b>вечный PRO</b> бесплатно! 🚀\n\n"
            "💎 <b>Что даёт статус PRO навсегда:</b>\n"
            "• 🚀 <b>Безлимитное скачивание</b> треков каждый день\n"
            "• 🎼 Студийное качество звука <b>WAV (HD)</b>\n"
            "• 🎬 Генерация видеоклипов <b>MP4</b> с анимацией\n"
            "• 🎛 Сведение треков через <b>DJ Crossfade</b>\n"
            "• 🎤 Разделение на <b>Stems (вокал / инструментал)</b>\n\n"
            "👉 <b>Как участвовать:</b>\n"
            "1. Быть подписанным на наш канал @youtubestantg\n"
            "2. Нажать кнопку <b>«🎉 Участвовать»</b> ниже\n\n"
            "<i>Итоги подведем уже скоро через генератор случайных чисел. Удачи каждому! 🎧</i>"
        ),
        "kk": (
            "🔥 <b>ЕСКЕРТУ: 20 МӘҢГІЛІК PRO-АККАУНТ ҰТЫС ОЙЫНЫ!</b> 🎁\n\n"
            f"1 000 қолданушыға арналған мерейтойлық байқауымызға қазірдің өзінде <b>{count_str} қатысушы</b> тіркелді!\n\n"
            "Егер әлі қатысу батырмасын баспаған болсаңыз — дәл қазір мүмкіндікті жіберіп алмаңыз! 🚀\n\n"
            "💎 <b>Мәңгілік PRO мәртебесі не береді:</b>\n"
            "• 🚀 Күн сайын тректерді <b>шектеусіз жүктеу</b>\n"
            "• 🎼 Студиялық таза <b>WAV (HD)</b> дыбыс сапасы\n"
            "• 🎬 Анимациясы бар <b>MP4 видеоклиптер</b>\n"
            "• 🎛 <b>DJ Crossfade</b> арқылы тректерді микстеу\n"
            "• 🎤 Тректі <b>Stems (вокал / минус)</b> бөліктеріне бөлу\n\n"
            "👉 <b>Қатысу шарттары:</b>\n"
            "1. Біздің @youtubestantg ресми арнамызға жазылу\n"
            "2. Төмендегі <b>«🎉 Қатысу»</b> батырмасын басу\n\n"
            "<i>Жеңімпаздар жақында кездейсоқ таңдау арқылы анықталады. Сәттілік! 🎧</i>"
        ),
        "en": (
            "🔥 <b>REMINDER: 20 LIFETIME PRO ACCOUNTS GIVEAWAY!</b> 🎁\n\n"
            f"Over <b>{count_str} participants</b> have already entered our 1,000 users milestone giveaway!\n\n"
            "If you haven't entered yet, don't miss your chance to win lifetime PRO! 🚀\n\n"
            "💎 <b>What Lifetime PRO gives you:</b>\n"
            "• 🚀 <b>Unlimited</b> daily track downloads\n"
            "• 🎼 Studio-grade <b>WAV (HD)</b> lossless audio\n"
            "• 🎬 Visualized <b>MP4 video clips</b>\n"
            "• 🎛 <b>DJ Crossfade</b> track mixing\n"
            "• 🎤 <b>Stems separation</b> (vocals / instrumental)\n\n"
            "👉 <b>How to participate:</b>\n"
            "1. Subscribe to our channel @youtubestantg\n"
            "2. Click the <b>«🎉 Enter Giveaway»</b> button below\n\n"
            "<i>Winners will be announced soon. Best of luck! 🎧</i>"
        ),
    }


def get_buttons(participant_count: int) -> dict[str, list]:
    count_label = f" ({participant_count})" if participant_count > 0 else ""
    return {
        "ru": [
            [{"text": f"🎉 Участвовать{count_label}", "callback_data": "contest:join:1000users"}],
            [{"text": "📢 Наш канал", "url": "https://t.me/youtubestantg"}],
        ],
        "kk": [
            [{"text": f"🎉 Қатысу{count_label}", "callback_data": "contest:join:1000users"}],
            [{"text": "📢 Біздің арна", "url": "https://t.me/youtubestantg"}],
        ],
        "en": [
            [{"text": f"🎉 Enter Giveaway{count_label}", "callback_data": "contest:join:1000users"}],
            [{"text": "📢 Our Channel", "url": "https://t.me/youtubestantg"}],
        ],
    }


async def main():
    dry_run = "--dry-run" in sys.argv
    test_admin = "--test-admin" in sys.argv

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing!")
        sys.exit(1)

    participant_count = get_participant_count()
    texts = get_texts(participant_count)
    buttons = get_buttons(participant_count)

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    if test_admin:
        print(f"Sending test reminder message to ADMIN_ID {ADMIN_ID}...")
        async with aiohttp.ClientSession() as session:
            payload = {
                "chat_id": ADMIN_ID,
                "text": texts["ru"],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": buttons["ru"]},
            }
            async with session.post(api_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                print("Admin test response:", data)
        return

    # Загружаем всех пользователей
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT user_id, COALESCE(language, 'ru')
        FROM users
        ORDER BY created_at ASC
    """)
    users = c.fetchall()
    conn.close()

    total = len(users)
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Total users in database: {total}. Contest participants: {participant_count}.")

    lock_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".broadcast_reminder_done")
    if os.path.exists(lock_file) and not test_admin and not dry_run:
        try:
            with open(lock_file, "r") as f:
                last_date = f.read().strip()
            if last_date == time.strftime("%Y-%m-%d"):
                print("Broadcast reminder was already sent today. Exiting.")
                return
        except Exception:
            pass

    if dry_run:
        print("DRY RUN finished. No messages sent.")
        return

    start_time = time.time()
    sent = 0
    blocked = 0
    failed = 0

    async with aiohttp.ClientSession() as session:
        for idx, (uid, lang) in enumerate(users, 1):
            text = texts.get(lang, texts["ru"])
            kb = buttons.get(lang, buttons["ru"])
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
                            print(f"Rate limit 429. Sleeping {retry_after}s...")
                            await asyncio.sleep(retry_after)
                            continue
                        else:
                            failed += 1
                            break
                except Exception as e:
                    if attempt == 2:
                        failed += 1
                    await asyncio.sleep(1)

            # Ограничение скорости Telegram API (25 сообщений в секунду)
            await asyncio.sleep(0.04)

            if idx % 50 == 0 or idx == total:
                print(f"Progress: {idx}/{total} (sent: {sent}, blocked: {blocked}, failed: {failed})")

    duration = int(time.time() - start_time)
    try:
        with open(lock_file, "w") as f:
            f.write(time.strftime("%Y-%m-%d"))
    except Exception:
        pass
    summary = (
        f"📢 <b>Отчет о рассылке-напоминании (Конкурс 20 PRO):</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"🎟 Участников конкурса: <b>{participant_count}</b>\n"
        f"✅ Успешно доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок отправки: <b>{failed}</b>\n"
        f"⏱ Время отправки: <b>{duration} сек</b>\n\n"
        f"<i>Для подведения итогов используйте:</i> <code>/finish_contest1000</code>"
    )
    print(summary)

    # Уведомляем администратора в Telegram
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
