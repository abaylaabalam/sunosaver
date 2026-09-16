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
                stems_count     INTEGER DEFAULT 0,
                PRIMARY KEY(user_id, date_str)
            )
        """)

        # Таблица стемов (вокал и минус)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS track_stems (
                song_id              TEXT PRIMARY KEY,
                vocals_file_id       TEXT NOT NULL,
                instrumental_file_id TEXT NOT NULL,
                created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

        # Таблица донатов и поддержки
        await db.execute("""
            CREATE TABLE IF NOT EXISTS donations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                username   TEXT,
                amount     INTEGER NOT NULL,
                currency   TEXT DEFAULT 'XTR',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            "ALTER TABLE users ADD COLUMN source TEXT DEFAULT 'direct'",
            "ALTER TABLE users ADD COLUMN downloads_count INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN is_subscribed INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN subscribed_at TIMESTAMP DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN last_active TIMESTAMP DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN custom_artist TEXT DEFAULT NULL",
            "ALTER TABLE track_cache ADD COLUMN artist TEXT DEFAULT NULL",
            "ALTER TABLE track_cache ADD COLUMN image_url TEXT DEFAULT NULL",
            "ALTER TABLE daily_usage ADD COLUMN stems_count INTEGER DEFAULT 0",
        ]:
            try:
                await db.execute(sql)
            except Exception:
                pass

        try:
            await db.execute("UPDATE users SET last_active = COALESCE(created_at, CURRENT_TIMESTAMP) WHERE last_active IS NULL")
        except Exception:
            pass

        # Миграция: заполняем source для пользователей по рефералке
        try:
            await db.execute("UPDATE users SET source = 'referral' WHERE referrer_id IS NOT NULL AND (source IS NULL OR source = 'direct')")
        except Exception:
            pass

        # Миграция: инициализируем downloads_count из user_tracks для существующих активных пользователей
        try:
            await db.execute("""
                UPDATE users 
                SET downloads_count = (SELECT COUNT(*) FROM user_tracks WHERE user_tracks.user_id = users.user_id)
                WHERE (downloads_count IS NULL OR downloads_count = 0)
                AND EXISTS (SELECT 1 FROM user_tracks WHERE user_tracks.user_id = users.user_id)
            """)
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
    source: str = "direct",
) -> tuple[str, bool, int | None]:
    """
    Регистрирует или возвращает пользователя с фиксацией источника трафика.
    Возвращает (language, is_new_user, effective_referrer_id).
    effective_referrer_id будет None, если пользователь уже был в БД или referrer_id невалиден.
    """
    clean_source = (source or "direct").strip()[:32]
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT language, source FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                lang, cur_source = row[0], row[1]
                # Если источник у существующего пользователя не был задан или был direct, а пришёл конкретный
                if clean_source != "direct" and (not cur_source or cur_source == "direct"):
                    await db.execute(
                        "UPDATE users SET source = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
                        (clean_source, user_id),
                    )
                else:
                    await db.execute(
                        "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?",
                        (user_id,),
                    )
                await db.commit()
                return lang, False, None

        # Проверяем, что referrer_id существует и не является самим пользователем
        effective_ref = None
        if referrer_id and referrer_id != user_id:
            async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (referrer_id,)) as r_cur:
                if await r_cur.fetchone():
                    effective_ref = referrer_id
                    if clean_source == "direct":
                        clean_source = "referral"

        await db.execute(
            """
            INSERT INTO users (user_id, language, referrer_id, source)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, fallback_lang, effective_ref, clean_source),
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
            "SELECT downloads_count, mixes_count, wav_count, stems_count FROM daily_usage WHERE user_id = ? AND date_str = ?",
            (user_id, today),
        ) as cursor:
            u_row = await cursor.fetchone()
            dl_today = u_row[0] if u_row else 0
            mix_today = u_row[1] if u_row else 0
            wav_today = u_row[2] if u_row else 0
            stems_today = u_row[3] if u_row and len(u_row) > 3 else 0

    needed = max(0, required_referrals - invited_count)
    return {
        "is_pro": is_pro,
        "invited_count": invited_count,
        "needed": needed,
        "downloads_today": dl_today,
        "mixes_today": mix_today,
        "wav_today": wav_today,
        "stems_today": stems_today,
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


async def increment_daily_usage(user_id: int, action: str) -> int:
    """Увеличивает дневной счетчик action ('downloads', 'mixes', 'wav'). Возвращает общий downloads_count пользователя."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    col = f"{action}_count"
    total_downloads = 0
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            f"""
            INSERT INTO daily_usage (user_id, date_str, {col})
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, date_str) DO UPDATE SET {col} = {col} + 1
            """,
            (user_id, today),
        )
        extra_sql = ", downloads_count = COALESCE(downloads_count, 0) + 1" if action == "downloads" else ""
        await db.execute(
            f"UPDATE users SET last_active = CURRENT_TIMESTAMP {extra_sql} WHERE user_id = ?",
            (user_id,),
        )
        if action == "downloads":
            async with db.execute("SELECT downloads_count FROM users WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    total_downloads = row[0]
        await db.commit()
    return total_downloads


async def save_donation(user_id: int, username: str | None, amount: int, currency: str = "XTR"):
    """Сохраняет донат пользователя."""
    clean_username = (username or f"User_{user_id}").lstrip("@")[:64]
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO donations (user_id, username, amount, currency)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, clean_username, amount, currency),
        )
        await db.commit()


async def get_top_donators(limit: int = 5) -> list[dict]:
    """Возвращает топ донаторов по общей сумме Stars."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            """
            SELECT username, SUM(amount) as total_amount
            FROM donations
            GROUP BY user_id
            ORDER BY total_amount DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [{"username": r[0], "amount": r[1]} for r in rows]


async def get_cached_stems(song_id: str) -> tuple[str, str] | None:
    """Возвращает (vocals_file_id, instrumental_file_id) если стемы есть в кэше."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT vocals_file_id, instrumental_file_id FROM track_stems WHERE song_id = ?",
            (song_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0] and row[1]:
                return (row[0], row[1])
    return None


async def save_track_stems(song_id: str, vocals_file_id: str, instrumental_file_id: str):
    """Сохраняет file_id вокала и минуса в кэш."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO track_stems (song_id, vocals_file_id, instrumental_file_id)
            VALUES (?, ?, ?)
            ON CONFLICT(song_id) DO UPDATE SET
                vocals_file_id = excluded.vocals_file_id,
                instrumental_file_id = excluded.instrumental_file_id,
                created_at = CURRENT_TIMESTAMP
            """,
            (song_id, vocals_file_id, instrumental_file_id),
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

async def get_cached_track(song_id: str) -> tuple[str, str | None, str | None, str | None, str | None] | None:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT file_id, title, lyrics, artist, image_url FROM track_cache WHERE song_id = ? AND file_id IS NOT NULL AND file_id != ''", (song_id,)
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
                return row[0], row[1], row[2], row[3], row[4]
            return None


async def get_user_custom_artist(user_id: int) -> str | None:
    """Возвращает кастомный никнейм исполнителя, заданный пользователем, или None."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT custom_artist FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None


async def set_user_custom_artist(user_id: int, artist: str | None):
    """Сохраняет или сбрасывает кастомный никнейм исполнителя для пользователя."""
    val = artist.strip()[:64] if artist and artist.strip() else None
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET custom_artist = ? WHERE user_id = ?",
            (val, user_id),
        )
        await db.commit()


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


async def save_track_cache(
    song_id: str,
    file_id: str,
    title: str,
    lyrics: str | None = None,
    artist: str | None = None,
    image_url: str | None = None,
):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """
            INSERT INTO track_cache
                (song_id, file_id, title, lyrics, artist, image_url, download_count, last_used)
            VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            ON CONFLICT(song_id) DO UPDATE SET
                file_id = excluded.file_id,
                title = excluded.title,
                lyrics = COALESCE(excluded.lyrics, track_cache.lyrics),
                artist = COALESCE(excluded.artist, track_cache.artist),
                image_url = COALESCE(excluded.image_url, track_cache.image_url),
                last_used = CURRENT_TIMESTAMP
            """,
            (song_id, file_id, title, lyrics, artist, image_url),
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

        async with db.execute("SELECT COALESCE(SUM(downloads_count), 0), COALESCE(SUM(mixes_count), 0), COALESCE(SUM(wav_count), 0), COALESCE(SUM(stems_count), 0) FROM daily_usage WHERE date_str = date('now')") as cursor:
            row = await cursor.fetchone()
            dl_today, mix_today, wav_today, stems_today = (row[0], row[1], row[2], row[3]) if row else (0, 0, 0, 0)

        # Реально поступившие донаты
        async with db.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM donations") as cursor:
            row = await cursor.fetchone()
            donations_count, donations_total = (row[0], row[1]) if row else (0, 0)

        # Всего миксов и WAV за всё время
        async with db.execute("SELECT COALESCE(SUM(mixes_count), 0), COALESCE(SUM(wav_count), 0) FROM daily_usage") as cursor:
            row = await cursor.fetchone()
            total_mixes, total_wavs = (row[0], row[1]) if row else (0, 0)

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

        # Реальные пользователи (скачали хотя бы 1 трек или есть в личной библиотеке)
        async with db.execute("""
            SELECT COUNT(*) FROM users 
            WHERE (downloads_count IS NOT NULL AND downloads_count > 0)
               OR EXISTS (SELECT 1 FROM user_tracks WHERE user_tracks.user_id = users.user_id)
        """) as cursor:
            row = await cursor.fetchone()
            real_users = row[0] if row else 0

        # Подтверждённые подписчики среди пользователей бота
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_subscribed = 1") as cursor:
            row = await cursor.fetchone()
            subscribed_users = row[0] if row else 0

        async with db.execute("SELECT COUNT(*) FROM users WHERE is_subscribed = 1 AND subscribed_at >= datetime('now', '-24 hours')") as cursor:
            row = await cursor.fetchone()
            subscribed_24h = row[0] if row else 0

        # Активные за последние 7 дней
        async with db.execute("SELECT COUNT(DISTINCT user_id) FROM daily_usage") as cursor:
            row = await cursor.fetchone()
            active_users_7d = row[0] if row else 0

        # Источники трафика (откуда пришли пользователи)
        async with db.execute("""
            SELECT 
                COALESCE(NULLIF(source, ''), 'direct') as src,
                COUNT(*) as total_count,
                COUNT(CASE WHEN (downloads_count IS NOT NULL AND downloads_count > 0) OR EXISTS (SELECT 1 FROM user_tracks WHERE user_tracks.user_id = users.user_id) THEN 1 END) as real_count
            FROM users
            GROUP BY src
            ORDER BY total_count DESC
            LIMIT 15
        """) as cursor:
            source_rows = await cursor.fetchall()
            sources = []
            for src, count, r_count in source_rows:
                pct = round((count / total_users * 100), 1) if total_users > 0 else 0
                real_pct = round((r_count / count * 100), 1) if count > 0 else 0
                sources.append({
                    "source": src,
                    "count": count,
                    "percent": pct,
                    "real_count": r_count,
                    "real_percent": real_pct,
                })

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
        "real_users": real_users,
        "new_users_24h": new_users_24h,
        "active_users_today": active_users_today,
        "active_users_7d": active_users_7d,
        "subscribed_users": subscribed_users,
        "subscribed_24h": subscribed_24h,
        "downloads_today": dl_today,
        "mixes_today": mix_today,
        "wav_today": wav_today,
        "stems_today": stems_today,
        "donations_count": donations_count,
        "donations_total": donations_total,
        "total_mixes": total_mixes,
        "total_wavs": total_wavs,
        "languages": languages,
        "sources": sources,
        "referral_users": referral_users,
        "pro_users": pro_users,
        "cached_tracks": cached_tracks,
        "cached_videos": cached_videos,
        "cached_wavs": cached_wavs,
        "banned_users": banned_count,
    }


async def set_user_subscribed(user_id: int, is_sub: bool):
    """Обновляет статус подписки пользователя на канал и дату фиксации."""
    async with aiosqlite.connect(DB_NAME) as db:
        if is_sub:
            await db.execute(
                """
                UPDATE users
                SET is_subscribed = 1,
                    subscribed_at = COALESCE(subscribed_at, CURRENT_TIMESTAMP)
                WHERE user_id = ?
                """,
                (user_id,),
            )
        else:
            await db.execute(
                """
                UPDATE users
                SET is_subscribed = 0
                WHERE user_id = ?
                """,
                (user_id,),
            )
        await db.commit()


async def get_app_setting(key: str, default: str | None = None) -> str | None:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default


async def set_app_setting(key: str, value: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def record_channel_subscriber_count(current_count: int) -> dict:
    """
    Фиксирует текущее число подписчиков канала в базе,
    сохраняет начальную базу (baseline) и возвращает динамику прироста.
    """
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. Baseline (первоначальное число подписчиков при старте учёта)
        async with db.execute("SELECT value FROM app_settings WHERE key = 'channel_subs_baseline'") as cur:
            row = await cur.fetchone()
            if not row:
                await db.execute("INSERT INTO app_settings (key, value) VALUES ('channel_subs_baseline', ?)", (str(current_count),))
                baseline = current_count
            else:
                try:
                    baseline = int(row[0])
                except Exception:
                    baseline = current_count

        # 2. Число подписчиков на начало сегодняшнего дня
        today = datetime.utcnow().strftime("%Y-%m-%d")
        today_key = f"channel_subs_start_{today}"
        async with db.execute("SELECT value FROM app_settings WHERE key = ?", (today_key,)) as cur:
            row = await cur.fetchone()
            if not row:
                await db.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (today_key, str(current_count)))
                today_start = current_count
            else:
                try:
                    today_start = int(row[0])
                except Exception:
                    today_start = current_count

        # Актуализируем последнее значение
        await db.execute(
            "INSERT INTO app_settings (key, value) VALUES ('channel_subs_latest', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(current_count),),
        )
        await db.commit()

        return {
            "current": current_count,
            "baseline": baseline,
            "gained_total": max(0, current_count - baseline),
            "gained_today": max(0, current_count - today_start),
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
        await db.execute(
            """
            UPDATE users SET 
                last_active = CURRENT_TIMESTAMP,
                downloads_count = COALESCE(downloads_count, 0) + 1
            WHERE user_id = ?
            """,
            (user_id,),
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