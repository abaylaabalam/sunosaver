#!/usr/bin/env python3
"""
Скрипт рассылки итогов юбилейного конкурса (20 вечных PRO-аккаунтов).
Отправляет результаты всем пользователям бота на их родном языке (ru, kk, en).
Поддерживает флаги:
  --dry-run      : Подсчет пользователей и проверка без реальной отправки.
  --test-admin   : Отправка тестового сообщения только администратору.
"""
import asyncio
import os
import sys
import sqlite3
import time
import urllib.parse
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
        return row[0] if row else 209
    except Exception as e:
        print(f"Error fetching participant count: {e}")
        return 209


def get_total_users_count() -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        row = c.fetchone()
        conn.close()
        return row[0] if row else 1400
    except Exception as e:
        print(f"Error fetching users count: {e}")
        return 1400


def get_texts(participant_count: int, total_users: int) -> dict[str, str]:
    approx_users = f"{total_users:,}".replace(",", " ")
    return {
        "ru": (
            "🏆 <b>ИТОГИ ЮБИЛЕЙНОГО РОЗЫГРЫША 20 ВЕЧНЫХ PRO!</b> 🎉\n\n"
            f"Друзья, нас в боте уже <b>более {approx_users} человек</b>! 🚀\n"
            "Огромное спасибо каждому из вас за то, что творите и слушаете музыку вместе с нами!\n\n"
            "📊 <b>Результаты конкурса:</b>\n"
            f"• Всего участников: <b>{participant_count} человек</b>\n"
            "• Разыграно: <b>20 вечных PRO-аккаунтов</b>\n\n"
            "🎲 Победители определены генератором случайных чисел. "
            "Каждому счастливчику уже пришло персональное уведомление в ЛС от бота, "
            "а статус <b>PRO НАВСЕГДА</b> успешно активирован! 💎\n\n"
            "───\n"
            "🎁 <b>Не выиграли в розыгрыше? Не расстраивайтесь!</b>\n"
            "Вы можете получить точно такой же <b>вечный PRO навсегда абсолютно бесплатно</b>:\n"
            "👉 Пригласите всего <b>3 друзей</b> в бота по вашей персональной ссылке!\n\n"
            "Нажмите кнопку ниже, чтобы отправить ссылку друзьям 👇"
        ),
        "kk": (
            "🏆 <b>20 МӘҢГІЛІК PRO ҰТЫС ОЙЫНЫНЫҢ ҚОРЫТЫНДЫСЫ!</b> 🎉\n\n"
            f"Достар, ботымызда <b>{approx_users}-ден астам қолданушы</b> болды! 🚀\n"
            "Әрқайсыңызға сенім мен қолдау үшін үлкен рақмет!\n\n"
            "📊 <b>Байқау нәтижесі:</b>\n"
            f"• Барлық қатысушылар: <b>{participant_count} адам</b>\n"
            "• Ойнатылды: <b>20 мәңгілік PRO-аккаунт</b>\n\n"
            "🎲 Жеңімпаздар кездейсоқ таңдау арқылы анықталды. "
            "Барлық жеңімпаздарға жеке хабарлама жіберілді және <b>МӘҢГІЛІК PRO</b> мәртебесі қосылды! 💎\n\n"
            "───\n"
            "🎁 <b>Ұтысқа ілікпей қалсаңыз — еш мұңаймаңыз!</b>\n"
            "Сіз дәл осындай <b>мәңгілік PRO-ны мүлдем тегін</b> ала аласыз:\n"
            "👉 Өз жеке сілтемеңізбен ботқа бар болғаны <b>3 досыңызды</b> шақырыңыз!\n\n"
            "Сілтемені достарыңызға жіберу үшін төмендегі батырманы басыңыз 👇"
        ),
        "en": (
            "🏆 <b>RESULTS: 20 LIFETIME PRO GIVEAWAY!</b> 🎉\n\n"
            f"Our community has surpassed <b>{approx_users} music creators</b>! 🚀\n"
            "Thank you so much to each of you for creating music and being part of SunoSaver!\n\n"
            "📊 <b>Giveaway Results:</b>\n"
            f"• Total participants: <b>{participant_count} entered</b>\n"
            "• Prizes: <b>20 Lifetime PRO Accounts</b>\n\n"
            "🎲 Winners were drawn randomly. "
            "All 20 winners have received a personal direct notification, "
            "and their <b>LIFETIME PRO</b> has been activated! 💎\n\n"
            "───\n"
            "🎁 <b>Didn't win this time? Don't worry!</b>\n"
            "You can still get <b>Lifetime PRO 100% free</b> anytime:\n"
            "👉 Invite just <b>3 friends</b> to the bot with your personal referral link!\n\n"
            "Tap the button below to share your link with friends 👇"
        ),
    }


def get_user_buttons(user_id: int, lang: str) -> list[list[dict]]:
    ref_url = f"https://t.me/sunosaver_bot?start=ref_{user_id}"
    share_texts = {
        "ru": "Скачивай треки с Suno AI в высоком качестве, создавай DJ-миксы и качай студийный WAV через бота! 🎧",
        "kk": "Suno AI әндерін жоғары сапада жүкте, DJ-микстер жаса және студиялық WAV ал! 🎧",
        "en": "Download Suno AI tracks in high quality, build DJ mixes and get studio WAV audio via bot! 🎧",
    }
    share_msg = share_texts.get(lang, share_texts["ru"])
    share_tg_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_url)}&text={urllib.parse.quote(share_msg)}"

    btn_labels = {
        "ru": {
            "share": "📤 Пригласить 3 друзей (Получить PRO)",
            "donate": "☕️ Поддержать проект (Tribute / Stars)",
            "channel": "📢 Наш официальный канал",
        },
        "kk": {
            "share": "📤 3 дос шақыру (Мәңгілік PRO алу)",
            "donate": "☕️ Жобаны қолдау (Tribute / Stars)",
            "channel": "📢 Біздің ресми арна",
        },
        "en": {
            "share": "📤 Invite 3 friends (Unlock PRO)",
            "donate": "☕️ Support Project (Tribute / Stars)",
            "channel": "📢 Our Official Channel",
        },
    }
    b = btn_labels.get(lang, btn_labels["ru"])

    return [
        [{"text": b["share"], "url": share_tg_url}],
        [{"text": b["donate"], "callback_data": "donate:stars"}],
        [{"text": b["channel"], "url": "https://t.me/youtubestantg"}],
    ]


async def main():
    dry_run = "--dry-run" in sys.argv
    test_admin = "--test-admin" in sys.argv

    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing!")
        sys.exit(1)

    participant_count = get_participant_count()
    total_users = get_total_users_count()
    texts = get_texts(participant_count, total_users)

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    if test_admin:
        print(f"Sending test contest results message to ADMIN_ID {ADMIN_ID}...")
        async with aiohttp.ClientSession() as session:
            payload = {
                "chat_id": ADMIN_ID,
                "text": texts["ru"],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": get_user_buttons(ADMIN_ID, "ru")},
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
            buttons = get_user_buttons(uid, lang)
            payload = {
                "chat_id": uid,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": buttons},
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

            if idx % 100 == 0 or idx == total:
                print(f"Progress: {idx}/{total} (sent: {sent}, blocked: {blocked}, failed: {failed})")

    duration = int(time.time() - start_time)
    summary = (
        f"🏆 <b>Отчет о рассылке итогов конкурса (20 PRO):</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"🎟 Участников конкурса: <b>{participant_count}</b>\n"
        f"✅ Успешно доставлено: <b>{sent}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок отправки: <b>{failed}</b>\n"
        f"⏱ Время отправки: <b>{duration} сек</b>"
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
