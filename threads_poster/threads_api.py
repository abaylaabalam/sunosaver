import logging
import asyncio
from typing import Optional, Dict, Any
import aiohttp

logger = logging.getLogger("threads_poster.api")

class ThreadsAPIClient:
    """
    Client for Meta Threads Graph API.
    Docs: https://developers.facebook.com/docs/threads/
    """
    BASE_URL = "https://graph.threads.net/v1.0"

    def __init__(self, access_token: str, user_id: Optional[str] = None):
        self.access_token = access_token.strip()
        self.user_id = user_id.strip() if user_id else ""

    async def get_user_info(self, session: Optional[aiohttp.ClientSession] = None) -> Dict[str, Any]:
        """Fetch user profile information (id, username) to auto-detect user_id."""
        url = f"{self.BASE_URL}/me"
        params = {
            "fields": "id,username,threads_profile_picture_url",
            "access_token": self.access_token
        }

        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        try:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                if resp.status != 200:
                    error_msg = data.get("error", {}).get("message", str(data))
                    raise RuntimeError(f"Threads API get_user_info error ({resp.status}): {error_msg}")
                self.user_id = data.get("id", "")
                return data
        finally:
            if close_session:
                await session.close()

    async def get_publishing_limit(self, session: Optional[aiohttp.ClientSession] = None) -> Dict[str, Any]:
        """Check quota usage for the 24-hour rolling window."""
        if not self.user_id:
            await self.get_user_info(session)

        url = f"{self.BASE_URL}/{self.user_id}/threads_publishing_limit"
        params = {
            "fields": "quota_usage,config",
            "access_token": self.access_token
        }

        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        try:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                if resp.status != 200:
                    error_msg = data.get("error", {}).get("message", str(data))
                    logger.warning(f"Could not fetch publishing limit: {error_msg}")
                    return {}
                return data
        finally:
            if close_session:
                await session.close()

    async def create_container(
        self,
        text: str,
        image_url: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None
    ) -> str:
        """
        Step 1: Create a media container for text, image or reply.
        Returns the container ID.
        """
        if not self.user_id:
            await self.get_user_info(session)

        url = f"{self.BASE_URL}/{self.user_id}/threads"
        payload = {
            "text": text,
            "access_token": self.access_token
        }
        if image_url:
            payload["media_type"] = "IMAGE"
            payload["image_url"] = image_url
        else:
            payload["media_type"] = "TEXT"

        if reply_to_id:
            payload["reply_to_id"] = reply_to_id

        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        try:
            async with session.post(url, data=payload) as resp:
                data = await resp.json()
                if resp.status != 200:
                    error_msg = data.get("error", {}).get("message", str(data))
                    raise RuntimeError(f"Failed to create Threads container ({resp.status}): {error_msg}")
                return data["id"]
        finally:
            if close_session:
                await session.close()

    async def publish_container(
        self,
        creation_id: str,
        session: Optional[aiohttp.ClientSession] = None
    ) -> str:
        """
        Step 2: Publish the created container.
        Returns the published Thread post ID.
        """
        if not self.user_id:
            await self.get_user_info(session)

        url = f"{self.BASE_URL}/{self.user_id}/threads_publish"
        payload = {
            "creation_id": creation_id,
            "access_token": self.access_token
        }

        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        try:
            async with session.post(url, data=payload) as resp:
                data = await resp.json()
                if resp.status != 200:
                    error_msg = data.get("error", {}).get("message", str(data))
                    raise RuntimeError(f"Failed to publish Threads container ({resp.status}): {error_msg}")
                return data["id"]
        finally:
            if close_session:
                await session.close()

    async def post_text(
        self,
        text: str,
        image_url: Optional[str] = None,
        reply_to_id: Optional[str] = None,
        session: Optional[aiohttp.ClientSession] = None
    ) -> str:
        """
        Convenience method: Creates container and publishes it immediately.
        Returns published Thread post ID.
        """
        container_id = await self.create_container(
            text=text,
            image_url=image_url,
            reply_to_id=reply_to_id,
            session=session
        )
        # Brief pause to ensure Meta's distributed backend has registered the container
        await asyncio.sleep(2.5)
        post_id = await self.publish_container(creation_id=container_id, session=session)
        return post_id

    async def refresh_access_token(self, session: Optional[aiohttp.ClientSession] = None) -> Dict[str, Any]:
        """
        Refresh a long-lived Threads User Access Token before it expires (valid for 60 days).
        """
        url = "https://graph.threads.net/refresh_access_token"
        params = {
            "grant_type": "th_refresh_token",
            "access_token": self.access_token
        }

        close_session = False
        if session is None:
            session = aiohttp.ClientSession()
            close_session = True

        try:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                if resp.status != 200:
                    error_msg = data.get("error", {}).get("message", str(data))
                    raise RuntimeError(f"Failed to refresh access token: {error_msg}")
                if "access_token" in data:
                    self.access_token = data["access_token"]
                return data
        finally:
            if close_session:
                await session.close()
