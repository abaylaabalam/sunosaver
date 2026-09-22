import os
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import aiosqlite

logger = logging.getLogger("threads_poster.storage")

class PostsStorage:
    """
    SQLite storage for post queue, tracking status, publishing IDs and deduplication.
    """
    def __init__(self, db_path: Path):
        self.db_path = str(db_path)

    async def init_db(self) -> None:
        """Create tables if they do not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS threads_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_hash TEXT UNIQUE NOT NULL,
                    content TEXT NOT NULL,
                    image_url TEXT,
                    status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'published', 'failed'
                    thread_post_id TEXT,
                    reply_post_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    published_at TIMESTAMP,
                    error_message TEXT
                )
            """)
            # Migration check: add image_url if table already existed without it
            try:
                await db.execute("ALTER TABLE threads_posts ADD COLUMN image_url TEXT")
            except Exception:
                pass
            await db.execute("CREATE INDEX IF NOT EXISTS idx_posts_status ON threads_posts (status)")
            await db.commit()

    @staticmethod
    def _compute_hash(text: str) -> str:
        return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()

    async def import_posts_from_file(self, file_path: Path) -> Tuple[int, int]:
        """
        Parse posts separated by '---' from text file and add to queue.
        Supports optional image directive:
        IMAGE: https://example.com/image.jpg
        """
        if not file_path.exists():
            logger.warning(f"Posts file not found at {file_path}")
            return 0, 0

        with open(file_path, "r", encoding="utf-8") as f:
            raw_text = f.read()

        # Split on line starting with '---'
        chunks = [c.strip() for c in raw_text.split("\n---")]
        added = 0
        skipped = 0

        await self.init_db()

        async with aiosqlite.connect(self.db_path) as db:
            for chunk in chunks:
                if not chunk:
                    continue
                # Extract image url if present
                lines = []
                image_url = None
                for line in chunk.splitlines():
                    clean_l = line.strip()
                    if clean_l.startswith("#"):
                        continue
                    if clean_l.upper().startswith("IMAGE:") or clean_l.upper().startswith("[IMAGE:"):
                        # Extract URL
                        val = clean_l.split(":", 1)[1].strip().rstrip("]")
                        if val.startswith("http"):
                            image_url = val
                            continue
                    lines.append(line)

                content = "\n".join(lines).strip()
                if not content:
                    continue

                c_hash = self._compute_hash(content + (image_url or ""))
                try:
                    await db.execute(
                        "INSERT INTO threads_posts (content_hash, content, image_url, status) VALUES (?, ?, ?, 'pending')",
                        (c_hash, content, image_url)
                    )
                    added += 1
                except aiosqlite.IntegrityError:
                    skipped += 1
            await db.commit()

        logger.info(f"Import finished: {added} new posts added, {skipped} skipped (duplicates).")
        return added, skipped

    async def get_next_pending(self) -> Optional[Dict[str, Any]]:
        """Retrieve the oldest pending post."""
        await self.init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT id, content_hash, content, image_url, status FROM threads_posts WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

    async def mark_published(
        self,
        post_id: int,
        thread_post_id: str,
        reply_post_id: Optional[str] = None
    ) -> None:
        """Mark post as successfully published."""
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE threads_posts
                SET status = 'published',
                    thread_post_id = ?,
                    reply_post_id = ?,
                    published_at = ?,
                    error_message = NULL
                WHERE id = ?
                """,
                (thread_post_id, reply_post_id, now, post_id)
            )
            await db.commit()

    async def mark_failed(self, post_id: int, error_message: str) -> None:
        """Mark post as failed with error message."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE threads_posts SET status = 'failed', error_message = ? WHERE id = ?",
                (error_message, post_id)
            )
            await db.commit()

    async def reset_all_to_pending(self) -> int:
        """Reset published/failed posts back to pending for queue recycling."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE threads_posts SET status = 'pending', error_message = NULL WHERE status != 'pending'"
            )
            await db.commit()
            return cursor.rowcount

    async def get_stats(self) -> Dict[str, int]:
        """Return counts of posts by status."""
        await self.init_db()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT status, COUNT(*) FROM threads_posts GROUP BY status")
            rows = await cursor.fetchall()
            stats = {"total": 0, "pending": 0, "published": 0, "failed": 0}
            for status, count in rows:
                if status in stats:
                    stats[status] = count
                stats["total"] += count
            return stats
