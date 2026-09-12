from datetime import datetime
import os
import aiosqlite

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db")


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Пользователи, их языки и реферальная система
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                language      TEXT    DEFAULT 'en',
                referrer_id   INTEGER DEFAULT NULL,
                is_pro        INTEGER DEFAULT 0,
                invited_count INTEGER DEFAULT 0,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Ежедневные лимиты использования
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id         INTEGER,
                date_str        TEXT,
                downloads_count INTEGER DEFAULT 0,
                mixes_count     INTEGER DEFAULT 0,
                wav_count       INTEGER DEFAULT 0,
                PRIMARY KEY(user_id, date_str)
            )
        """)

        # Кэш треков
        await db.execute("""
            CREATE TABLE IF NOT EXISTS track_cache (
                song_id        TEXT    PRIMARY KEY,
                file_id        TEXT    NOT NULL,
                title          TEXT,
                lyrics         TEXT,
                wav_file_id    TEXT,
                video_file_id  TEXT,
                download_count INTEGER DEFAULT 1,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_used      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Заблокированные пользователи
        await db.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id   INTEGER PRIMARY KEY,
                reason    TEXT    DEFAULT '',
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Личные треки пользователей для миксов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_tracks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                song_id    TEXT    NOT NULL,
                title      TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, song_id)
            )
        """)

        # Глобальная статистика (одна строка, id=1)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                id              INTEGER PRIMARY KEY CHECK (id = 1),
                total_downloads INTEGER DEFAULT 0
            )
        """)
        await db.execute("INSERT OR IGNORE INTO stats (id, total_downloads) VALUES (1, 0)")

        # Настройки приложения и флаги
        await db.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Миграция: добавляем новые колонки в users и track_cache для существующих БД
        for sql in [
            "ALTER TABLE track_cache ADD COLUMN download_count INTEGER DEFAULT 1",
            "ALTER TABLE track_cache ADD COLUMN last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE track_cache ADD COLUMN lyrics TEXT",
            "ALTER TABLE track_cache ADD COLUMN wav_file_id TEXT",
            "ALTER TABLE track_cache ADD COLUMN video_file_id TEXT",
            "ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN is_pro INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN invited_count INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(sql)
            except Exception:
                pass

        await db.commit()


# ─── Язык пользователя ────────────────────────────────────────────────────────

async def get_user_language(user_id: int, fallback_lang: str) -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT language FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0]
            await db.execute(
                "INSERT INTO users (user_id, language) VALUES (?, ?)",
                (user_id, fallback_lang),
            )
            await db.commit()
            return fallback_lang


async def set_user_language(user_id: int, lang: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, language) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET language = excluded.language
            """,
            (user_id, lang),
        )
        await db.commit()


async def register_or_get_user(
    user_id: int,
    fallback_lang: str,
    referrer_id: int | None = None,
) -> tuple[str, bool, int | None]:
    """
    Регистрирует или возвращает пользователя.
    Возвращает (language, is_new_user, effective_referrer_id).
    effective_referrer_id будет None, если пользователь уже был в БД или referrer_id невалиден.
    """
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT language FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], False, None

        # Проверяем, что referrer_id существует и не является самим пользователем
        effective_ref = None
        if referrer_id and referrer_id != user_id:
            async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (referrer_id,)) as r_cur:
                if await r_cur.fetchone():
                    effective_ref = referrer_id

        await db.execute(
            """
            INSERT INTO users (user_id, language, referrer_id)
            VALUES (?, ?, ?)
            """,
            (user_id, fallback_lang, effective_ref),
        )
        await db.commit()
        return fallback_lang, True, effective_ref


async def add_referral_and_check_pro(
    referrer_id: int,
    required_referrals: int = 3,
) -> tuple[int, bool]:
    """
    Увеличивает счетчик приглашенных у referrer_id.
    Возвращает (new_invited_count, became_pro_just_now: bool).
    """
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET invited_count = invited_count + 1 WHERE user_id = ?",
            (referrer_id,),
        )
        async with db.execute(
            "SELECT invited_count, is_pro FROM users WHERE user_id = ?",
            (referrer_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                await db.commit()
                return 0, False

            invited_count, is_pro = row[0] or 0, bool(row[1])
            became_pro = False
            if invited_count >= required_referrals and not is_pro:
                await db.execute("UPDATE users SET is_pro = 1 WHERE user_id = ?", (referrer_id,))
                became_pro = True

            await db.commit()
            return invited_count, became_pro


async def is_user_pro(user_id: int, admin_id: int = 0) -> bool:
    """Проверяет, является ли пользователь PRO-аккаунтом (администратор всегда PRO)."""
    if admin_id and user_id == admin_id:
        return True
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT is_pro FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row and row[0] else False


async def get_user_pro_info(
    user_id: int,
    admin_id: int = 0,
    required_referrals: int = 3,
) -> dict:
    """Возвращает информацию о статусе PRO, количестве приглашённых и дневном использовании."""
    is_pro = True if (admin_id and user_id == admin_id) else False
    invited_count = 0
    today = datetime.utcnow().strftime("%Y-%m-%d")

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT is_pro, invited_count FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                if not is_pro:
                    is_pro = bool(row[0])
                invited_count = row[1] or 0

        async with db.execute(
            "SELECT downloads_count, mixes_count, wav_count FROM daily_usage WHERE user_id = ? AND date_str = ?",
            (user_id, today),
        ) as cursor:
            u_row = await cursor.fetchone()
            dl_today = u_row[0] if u_row else 0
            mix_today = u_row[1] if u_row else 0
            wav_today = u_row[2] if u_row else 0

    needed = max(0, required_referrals - invited_count)
    return {
        "is_pro": is_pro,
        "invited_count": invited_count,
        "needed": needed,
        "downloads_today": dl_today,
        "mixes_today": mix_today,
        "wav_today": wav_today,
    }


async def check_daily_limit(
    user_id: int,
    action: str,  # "downloads", "mixes", "wav"
    limit: int,
    is_pro: bool,
) -> tuple[bool, int]:
    """
    Возвращает (allowed: bool, current_usage: int).
    Для PRO-пользователей всегда возвращает (True, 0).
    """
    if is_pro:
        return True, 0

    today = datetime.utcnow().strftime("%Y-%m-%d")
    col = f"{action}_count"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            f"SELECT {col} FROM daily_usage WHERE user_id = ? AND date_str = ?",
            (user_id, today),
        ) as cursor:
            row = await cursor.fetchone()
            current = row[0] if row else 0
            return current < limit, current


async def increment_daily_usage(user_id: int, action: str):
    """Увеличивает дневной счетчик action ('downloads', 'mixes', 'wav')."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    col = f"{action}_count"
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            f"""
            INSERT INTO daily_usage (user_id, date_str, {col})
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, date_str) DO UPDATE SET {col} = {col} + 1
            """,
            (user_id, today),
        )
        await db.commit()


async def get_all_users() -> list[int]:
    """Возвращает список всех user_id незаблокированных пользователей для рассылки."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("""
            SELECT u.user_id FROM users u
            LEFT JOIN banned_users b ON u.user_id = b.user_id
            WHERE b.user_id IS NULL
        """) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


# ─── Кэш треков ───────────────────────────────────────────────────────────────

async def get_cached_track(song_id: str) -> tuple[str, str | None, str | None] | None:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT file_id, title, lyrics FROM track_cache WHERE song_id = ?", (song_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                await db.execute(
                    """UPDATE track_cache
                       SET download_count = download_count + 1,
                           last_used      = CURRENT_TIMESTAMP
                     WHERE song_id = ?""",
                    (song_id,),
                )
                await db.commit()
                return row[0], row[1], row[2]
            return None


async def get_track_lyrics(song_id: str) -> str | None:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT lyrics FROM track_cache WHERE song_id = ?", (song_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
            return None


async def get_cached_wav(song_id: str) -> str | None:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT wav_file_id FROM track_cache WHERE song_id = ?", (song_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
            return None


async def save_wav_cache(song_id: str, wav_file_id: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO track_cache (song_id, file_id, wav_file_id)
            VALUES (?, '', ?)
            ON CONFLICT(song_id) DO UPDATE SET wav_file_id = excluded.wav_file_id
            """,
            (song_id, wav_file_id),
        )
        await db.commit()


async def get_cached_video(song_id: str) -> str | None:
    """Возвращает Telegram video_file_id, если видео уже кэшировано."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT video_file_id FROM track_cache WHERE song_id = ?", (song_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
            return None


async def save_video_cache(song_id: str, video_file_id: str):
    """Сохраняет Telegram video_file_id в кэш."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO track_cache (song_id, file_id, video_file_id)
            VALUES (?, '', ?)
            ON CONFLICT(song_id) DO UPDATE SET video_file_id = excluded.video_file_id
            """,
            (song_id, video_file_id),
        )
        await db.commit()


async def save_track_cache(song_id: str, file_id: str, title: str, lyrics: str | None = None):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO track_cache
                (song_id, file_id, title, lyrics, download_count, last_used)
            VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(song_id) DO UPDATE SET
                file_id = excluded.file_id,
                title = excluded.title,
                lyrics = COALESCE(excluded.lyrics, track_cache.lyrics),
                last_used = CURRENT_TIMESTAMP
            """,
            (song_id, file_id, title, lyrics),
        )
        await db.execute(
            "UPDATE stats SET total_downloads = total_downloads + 1 WHERE id = 1"
        )
        await db.commit()


async def increment_total_downloads():
    """Считает раздачу из кэша тоже."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE stats SET total_downloads = total_downloads + 1 WHERE id = 1"
        )
        await db.commit()


async def cleanup_old_cache(days: int = 30) -> int:
    """Удаляет записи, к которым не обращались > `days` дней.
    Возвращает количество удалённых записей.
    Безопасно работает даже если колонка last_used ещё не мигрирована."""
    cutoff = f"-{days} days"
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            # Пробуем с last_used (новая схема)
            try:
                async with db.execute(
                    "SELECT COUNT(*) FROM track_cache WHERE last_used < datetime('now', ?)",
                    (cutoff,),
                ) as cursor:
                    row = await cursor.fetchone()
                    count = row[0] if row else 0
                if count:
                    await db.execute(
                        "DELETE FROM track_cache WHERE last_used < datetime('now', ?)",
                        (cutoff,),
                    )
                # Очищаем устаревшие записи дневных лимитов (старше 7 дней)
                await db.execute("DELETE FROM daily_usage WHERE date_str < date('now', '-7 days')")
                await db.commit()
                return count
            except Exception:
                return 0
    except Exception:
        return 0


# ─── Статистика ────────────────────────────────────────────────────────────────

async def get_stats() -> dict:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT total_downloads FROM stats WHERE id = 1"
        ) as cursor:
            row = await cursor.fetchone()
            total_downloads = row[0] if row else 0

        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            total_users = row[0] if row else 0

        async with db.execute("SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-24 hours')") as cursor:
            row = await cursor.fetchone()
            new_users_24h = row[0] if row else 0

        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM daily_usage WHERE date_str = date('now')") as cursor:
            row = await cursor.fetchone()
            active_users_today = row[0] if row else 0

        async with db.execute("SELECT COALESCE(SUM(downloads_count), 0), COALESCE(SUM(mixes_count), 0), COALESCE(SUM(wav_count), 0) FROM daily_usage WHERE date_str = date('now')") as cursor:
            row = await cursor.fetchone()
            dl_today, mix_today, wav_today = (row[0], row[1], row[2]) if row else (0, 0, 0)

        async with db.execute("SELECT language, COUNT(*) FROM users GROUP BY language ORDER BY COUNT(*) DESC") as cursor:
            lang_rows = await cursor.fetchall()
            languages = []
            for l_code, count in lang_rows:
                pct = round((count / total_users * 100), 1) if total_users > 0 else 0
                languages.append({"lang": l_code, "count": count, "percent": pct})

        async with db.execute("SELECT COUNT(*) FROM track_cache") as cursor:
            row = await cursor.fetchone()
            cached_tracks = row[0] if row else 0

        async with db.execute("SELECT COUNT(*) FROM track_cache WHERE video_file_id IS NOT NULL AND video_file_id != ''") as cursor:
            row = await cursor.fetchone()
            cached_videos = row[0] if row else 0

        async with db.execute("SELECT COUNT(*) FROM track_cache WHERE wav_file_id IS NOT NULL AND wav_file_id != ''") as cursor:
            row = await cursor.fetchone()
            cached_wavs = row[0] if row else 0

        async with db.execute("SELECT COUNT(*) FROM users WHERE referrer_id IS NOT NULL") as cursor:
            row = await cursor.fetchone()
            referral_users = row[0] if row else 0

        async with db.execute("SELECT COUNT(*) FROM users WHERE is_pro = 1") as cursor:
            row = await cursor.fetchone()
            pro_users = row[0] if row else 0

        async with db.execute("SELECT COUNT(*) FROM banned_users") as cursor:
            row = await cursor.fetchone()
            banned_count = row[0] if row else 0

    return {
        "total_downloads": total_downloads,
        "total_users": total_users,
        "new_users_24h": new_users_24h,
        "active_users_today": active_users_today,
        "downloads_today": dl_today,
        "mixes_today": mix_today,
        "wav_today": wav_today,
        "languages": languages,
        "referral_users": referral_users,
        "pro_users": pro_users,
        "cached_tracks": cached_tracks,
        "cached_videos": cached_videos,
        "cached_wavs": cached_wavs,
        "banned_users": banned_count,
    }



# ─── Блокировка пользователей ─────────────────────────────────────────────────

async def is_user_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None


async def ban_user(user_id: int, reason: str = ""):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO banned_users (user_id, reason) VALUES (?, ?)",
            (user_id, reason),
        )
        await db.commit()


async def unban_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "DELETE FROM banned_users WHERE user_id = ?", (user_id,)
        )
        await db.commit()


# ─── Библиотека пользователя (для миксов) ─────────────────────────────────────

async def save_user_track(user_id: int, song_id: str, title: str):
    """Сохраняет скачанный трек в личную библиотеку пользователя."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO user_tracks (user_id, song_id, title)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, song_id) DO UPDATE SET
                title = excluded.title,
                created_at = CURRENT_TIMESTAMP
            """,
            (user_id, song_id, title),
        )
        await db.commit()


async def get_user_tracks_count(user_id: int) -> int:
    """Возвращает общее количество треков пользователя в библиотеке."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM user_tracks WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_user_recent_tracks(user_id: int, limit: int = 10, offset: int = 0) -> list[tuple[str, str]]:
    """Возвращает [(song_id, title), ...] треков пользователя с пагинацией (новые первые)."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT song_id, title FROM user_tracks
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [(r[0], r[1] or "Suno Track") for r in rows]


# ─── Системные настройки и промо ──────────────────────────────────────────────

async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        await db.commit()


async def is_promo_100_awarded() -> bool:
    val = await get_setting("promo_100_awarded", "0")
    return val == "1"


async def get_total_users_count() -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def award_pro_to_first_n_users(n: int = 100) -> int:
    """Выдает вечный PRO первым n зарегистрированным пользователям."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            UPDATE users SET is_pro = 1
            WHERE user_id IN (
                SELECT user_id FROM users ORDER BY created_at ASC LIMIT ?
            )
            """,
            (n,),
        )
        await db.commit()
        await set_setting("promo_100_awarded", "1")
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_pro = 1") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_first_n_users_with_lang(n: int = 100) -> list[tuple[int, str]]:
    """Возвращает [(user_id, language), ...] для первых n пользователей."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT user_id, COALESCE(language, 'ru')
            FROM users
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (n,),
        ) as cursor:
            return await cursor.fetchall()