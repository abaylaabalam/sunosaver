import asyncio
import logging
import random
from typing import Optional, Dict, Any
from datetime import datetime

from .config import ThreadsConfig
from .threads_api import ThreadsAPIClient
from .storage import PostsStorage

logger = logging.getLogger("threads_poster.scheduler")

class ThreadsScheduler:
    """
    Orchestrates queue consumption and automated posting to Threads.
    """
    def __init__(self, config: Optional[ThreadsConfig] = None):
        self.config = config or ThreadsConfig()
        self.api = ThreadsAPIClient(
            access_token=self.config.ACCESS_TOKEN,
            user_id=self.config.USER_ID
        )
        self.storage = PostsStorage(db_path=self.config.DB_PATH)
        self.is_running = False

    async def publish_one(self) -> Optional[Dict[str, Any]]:
        """
        Fetches the next pending post, publishes it, and optionally adds a reply with bot link.
        """
        if not self.config.ACCESS_TOKEN:
            raise ValueError(
                "THREADS_ACCESS_TOKEN is not configured! Please add it to your .env file."
            )

        post = await self.storage.get_next_pending()
        if not post:
            if self.config.RECYCLE_POSTS:
                logger.info("Queue is empty. RECYCLE_POSTS is active — resetting queue...")
                reset_count = await self.storage.reset_all_to_pending()
                if reset_count > 0:
                    post = await self.storage.get_next_pending()
            if not post:
                logger.warning("No pending posts left in queue. Add more posts to posts.txt and run import.")
                return None

        post_id = post["id"]
        content = post["content"]
        image_url = post.get("image_url")

        # Threads text limit is 500 characters
        if len(content) > 500:
            logger.warning(f"Post #{post_id} exceeds 500 characters ({len(content)}). Truncating...")
            content = content[:497] + "..."

        logger.info(f"Publishing post #{post_id} ({len(content)} chars, image: {bool(image_url)})...")

        try:
            # 1. Publish main post
            thread_post_id = await self.api.post_text(text=content, image_url=image_url)
            logger.info(f"Post #{post_id} published successfully! Thread ID: {thread_post_id}")

            # 2. Optionally publish first comment with link (safe mode: 5-10 min delay + varied text)
            reply_post_id = None
            if self.config.AUTO_FIRST_REPLY and self.config.TELEGRAM_BOT_URL:
                # Pool of natural varied comments to prevent spam detection
                reply_variants = [
                    f"built the tool for this here: {self.config.TELEGRAM_BOT_URL}",
                    f"you can grab stems and wavs free with our bot: {self.config.TELEGRAM_BOT_URL}",
                    f"full stem splitter and wav extractor link: {self.config.TELEGRAM_BOT_URL}",
                    f"made it completely free on telegram: {self.config.TELEGRAM_BOT_URL}",
                    f"bot link if anyone wants to try it: {self.config.TELEGRAM_BOT_URL}",
                ]
                reply_text = random.choice(reply_variants)
                delay_sec = random.randint(300, 600)  # 5 to 10 minutes delay
                logger.info(f"Waiting {delay_sec // 60}m before posting reply comment to avoid spam detection...")
                await asyncio.sleep(delay_sec)
                try:
                    reply_post_id = await self.api.post_text(
                        text=reply_text,
                        reply_to_id=thread_post_id
                    )
                    logger.info(f"Reply published! Reply ID: {reply_post_id}")
                except Exception as reply_err:
                    logger.error(f"Failed to publish reply: {reply_err}")

            # 3. Mark in DB
            await self.storage.mark_published(
                post_id=post_id,
                thread_post_id=thread_post_id,
                reply_post_id=reply_post_id
            )

            return {
                "post_id": post_id,
                "thread_post_id": thread_post_id,
                "reply_post_id": reply_post_id,
                "content": content
            }

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to publish post #{post_id}: {error_msg}")
            await self.storage.mark_failed(post_id=post_id, error_message=error_msg)
            raise

    async def start(self) -> None:
        """
        Continuous scheduler loop. Publishes posts at randomized intervals.
        """
        self.is_running = True
        logger.info("Starting Threads Auto-poster daemon...")
        logger.info(
            f"Target: ~{self.config.POSTS_PER_DAY} posts/day. "
            f"Interval: {self.config.INTERVAL_MINUTES_MIN} - {self.config.INTERVAL_MINUTES_MAX} minutes."
        )

        while self.is_running:
            stats = await self.storage.get_stats()
            logger.info(
                f"Queue Status: {stats['pending']} pending, {stats['published']} published, {stats['failed']} failed."
            )

            try:
                result = await self.publish_one()
                if not result and not self.config.RECYCLE_POSTS:
                    logger.info("Queue is empty and recycling is disabled. Waiting 10 minutes before rechecking...")
                    await asyncio.sleep(600)
                    continue
            except Exception as e:
                logger.error(f"Error during publishing: {e}. Will retry on next tick.")

            # Calculate randomized sleep time
            interval_min = self.config.INTERVAL_MINUTES_MIN * 60
            interval_max = self.config.INTERVAL_MINUTES_MAX * 60
            sleep_seconds = random.randint(min(interval_min, interval_max), max(interval_min, interval_max))
            
            wake_time = datetime.fromtimestamp(datetime.now().timestamp() + sleep_seconds).strftime('%H:%M:%S')
            logger.info(f"Next post in {sleep_seconds // 60}m {sleep_seconds % 60}s (at ~{wake_time}). Sleeping...")
            
            try:
                await asyncio.sleep(sleep_seconds)
            except asyncio.CancelledError:
                logger.info("Scheduler received stop signal.")
                self.is_running = False
                break

    def stop(self) -> None:
        """Stop the running scheduler loop."""
        self.is_running = False
