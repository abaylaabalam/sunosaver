import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class ThreadsConfig:
    # Meta Threads API credentials
    ACCESS_TOKEN: str = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
    USER_ID: str = os.getenv("THREADS_USER_ID", "").strip()

    # Posting pace & scheduling
    POSTS_PER_DAY: int = int(os.getenv("THREADS_POSTS_PER_DAY", "50"))
    INTERVAL_MINUTES_MIN: int = int(os.getenv("THREADS_INTERVAL_MINUTES_MIN", "22"))
    INTERVAL_MINUTES_MAX: int = int(os.getenv("THREADS_INTERVAL_MINUTES_MAX", "35"))

    # Link & reply strategy
    TELEGRAM_BOT_URL: str = os.getenv("THREADS_TELEGRAM_BOT_URL", "").strip()
    AUTO_FIRST_REPLY: bool = os.getenv("THREADS_AUTO_FIRST_REPLY", "false").lower() in ("true", "1", "yes")
    FIRST_REPLY_TEMPLATE: str = os.getenv(
        "THREADS_FIRST_REPLY_TEMPLATE",
        "✨ Download in studio WAV / stems & grab lyrics with our free Telegram bot: {url}"
    )

    # Queue behavior: if True, re-queues posted messages when queue is empty
    RECYCLE_POSTS: bool = os.getenv("THREADS_RECYCLE_POSTS", "false").lower() in ("true", "1", "yes")

    # Files and DB paths
    DB_PATH: Path = Path(__file__).resolve().parent / "threads_queue.db"
    POSTS_FILE: Path = Path(__file__).resolve().parent / "posts.txt"
