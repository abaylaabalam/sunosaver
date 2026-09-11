import aiosqlite

DB_NAME = "bot_data.db"


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Пользователи и их языки
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id  INTEGER PRIMARY KEY,
                language TEXT    DEFAULT 'en',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

        # Автомиграция: добавляем ранее скачанные треки из кэша в библиотеку админа
        try:
            import os
            admin_id_env = int(os.getenv("ADMIN_ID", 0))
            if admin_id_env:
                await db.execute("""
                    INSERT OR IGNORE INTO user_tracks (user_id, song_id, title, created_at)
                    SELECT ?, song_id, COALESCE(title, 'Suno Track'), created_at
                    FROM track_cache
                """, (admin_id_env,))
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


# ─── Кэш треков ───────────────────────────────────────────────────────────────

async def get_cached_track(song_id: str) -> tuple[str, str | None, str | None] | None:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT file_id, title, lyrics FROM track_cache WHERE song_id = ?", (song_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                # Обновляем счётчик и время последнего использования
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
                    await db.commit()
                return count
            except Exception:
                # Колонка last_used ещё не создана — пропускаем очистку
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

        async with db.execute("SELECT COUNT(*) FROM track_cache") as cursor:
            row = await cursor.fetchone()
            cached_tracks = row[0] if row else 0

        async with db.execute("SELECT COUNT(*) FROM banned_users") as cursor:
            row = await cursor.fetchone()
            banned_count = row[0] if row else 0

    return {
        "total_downloads": total_downloads,
        "total_users": total_users,
        "cached_tracks": cached_tracks,
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