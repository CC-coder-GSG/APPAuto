from __future__ import annotations

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_markdown(markdown: str) -> None:
    if not settings.wecom_webhook_url:
        logger.info("Skip wecom push because webhook is empty")
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                settings.wecom_webhook_url,
                json={"msgtype": "markdown", "markdown": {"content": markdown}},
            )
            response.raise_for_status()
    except Exception:
        logger.exception("Failed to send wecom markdown")
