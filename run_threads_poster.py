#!/usr/bin/env python3
import sys
import asyncio
import argparse
import logging
from threads_poster.config import ThreadsConfig
from threads_poster.storage import PostsStorage
from threads_poster.threads_api import ThreadsAPIClient
from threads_poster.scheduler import ThreadsScheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("threads_poster.cli")


async def cmd_import():
    storage = PostsStorage(ThreadsConfig.DB_PATH)
    posts_file = ThreadsConfig.POSTS_FILE
    print(f"📥 Importing posts from: {posts_file}")
    added, skipped = await storage.import_posts_from_file(posts_file)
    print(f"✅ Added: {added} new posts.")
    print(f"⏩ Skipped (duplicates): {skipped}.")
    stats = await storage.get_stats()
    print(f"📊 Current queue status: {stats['pending']} pending | {stats['published']} published | {stats['failed']} failed (Total: {stats['total']})")


async def cmd_stats():
    storage = PostsStorage(ThreadsConfig.DB_PATH)
    stats = await storage.get_stats()
    print("=" * 45)
    print("           THREADS QUEUE STATS")
    print("=" * 45)
    print(f"  Pending (ready to post): {stats['pending']}")
    print(f"  Published:              {stats['published']}")
    print(f"  Failed:                 {stats['failed']}")
    print(f"  Total in database:      {stats['total']}")
    print("=" * 45)
    print(f"  Target speed:           ~{ThreadsConfig.POSTS_PER_DAY} posts / day")
    print(f"  Interval:               {ThreadsConfig.INTERVAL_MINUTES_MIN} - {ThreadsConfig.INTERVAL_MINUTES_MAX} min")
    print(f"  Auto first reply:       {'Enabled' if ThreadsConfig.AUTO_FIRST_REPLY else 'Disabled'}")
    print(f"  Telegram link:          {ThreadsConfig.TELEGRAM_BOT_URL or '(not set)'}")
    print("=" * 45)


async def cmd_check_token():
    if not ThreadsConfig.ACCESS_TOKEN:
        print("❌ THREADS_ACCESS_TOKEN is not set in your .env file!")
        return

    client = ThreadsAPIClient(access_token=ThreadsConfig.ACCESS_TOKEN, user_id=ThreadsConfig.USER_ID)
    print("🔍 Connecting to Meta Threads API...")
    try:
        user_info = await client.get_user_info()
        print("✅ Token is VALID!")
        print(f"👤 Username: @{user_info.get('username', 'unknown')}")
        print(f"🆔 User ID:  {user_info.get('id', 'unknown')}")

        limit_info = await client.get_publishing_limit()
        if limit_info:
            quota = limit_info.get("data", [{}])[0].get("quota_usage", "N/A")
            config = limit_info.get("data", [{}])[0].get("config", {})
            max_quota = config.get("quota_total", 250)
            print(f"📈 24h Quota Usage: {quota} / {max_quota} posts used")
    except Exception as e:
        print(f"❌ Error connecting to Threads API: {e}")


async def cmd_test():
    scheduler = ThreadsScheduler()
    print("🚀 Publishing 1 test post from queue...")
    try:
        res = await scheduler.publish_one()
        if res:
            print(f"🎉 Success! Thread Post ID: {res['thread_post_id']}")
            if res.get("reply_post_id"):
                print(f"💬 Reply Post ID: {res['reply_post_id']}")
        else:
            print("⚠️ No post was published (queue is empty).")
    except Exception as e:
        print(f"❌ Failed to publish post: {e}")


async def cmd_start():
    # First auto-import if any new posts in posts.txt
    storage = PostsStorage(ThreadsConfig.DB_PATH)
    await storage.import_posts_from_file(ThreadsConfig.POSTS_FILE)

    scheduler = ThreadsScheduler()
    try:
        await scheduler.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        scheduler.stop()
        print("\n👋 Auto-poster stopped.")


async def cmd_reset():
    storage = PostsStorage(ThreadsConfig.DB_PATH)
    count = await storage.reset_all_to_pending()
    print(f"🔄 Reset {count} posts back to 'pending'.")


def main():
    parser = argparse.ArgumentParser(description="Threads Auto-Poster CLI for SunoSaver")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("import", help="Import new posts from posts.txt into database queue")
    subparsers.add_parser("stats", help="Display queue status and posting configuration")
    subparsers.add_parser("check-token", help="Test Meta Threads API token and check quota")
    subparsers.add_parser("test", help="Publish 1 single post immediately for testing")
    subparsers.add_parser("start", help="Start continuous auto-posting daemon")
    subparsers.add_parser("reset", help="Reset all published posts back to pending")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "import": cmd_import,
        "stats": cmd_stats,
        "check-token": cmd_check_token,
        "test": cmd_test,
        "start": cmd_start,
        "reset": cmd_reset,
    }

    asyncio.run(commands[args.command]())


if __name__ == "__main__":
    main()
