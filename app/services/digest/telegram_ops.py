"""Telegram operations for the permanent pinned digest message.

Ownership rule: this subsystem manages exactly ONE message — the digest
message whose id it persisted. It edits that message in place, pins it
silently, and re-pins it if it was manually unpinned. It never unpins,
deletes, or otherwise touches any other message: the ``unpinChatMessage``
and ``unpinAllChatMessages`` API methods are deliberately never called
anywhere in this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.exceptions.custom_exceptions import TelegramPublishError
from app.log.logger import get_logger
from app.services.telegram.publisher import TelegramPublisher

logger = get_logger(__name__)

# Substrings of Telegram 400 descriptions meaning the persisted digest
# message no longer exists or can never be edited again — the only case
# in which a single replacement message is created.
_GONE_MARKERS = ("message to edit not found", "message can't be edited")


@dataclass(frozen=True)
class DigestPublishResult:
    """Outcome of one digest publish attempt.

    ``pinned`` is True when our pin was verified or established this run
    (a successful pin call, or getChat showing our message as the newest
    pin). False means unverified — e.g. a human admin's newer pin sits on
    top, which is left untouched.
    """

    message_id: str
    created: bool
    pinned: bool
    unchanged: bool


class DigestTelegramOps:
    def __init__(self, publisher: TelegramPublisher | None = None) -> None:
        self._owns_publisher = publisher is None
        self._publisher = publisher if publisher is not None else TelegramPublisher()

    async def publish_digest(
        self, text: str, existing_message_id: str | None
    ) -> DigestPublishResult:
        if existing_message_id is None:
            return await self._create_and_pin(text)

        try:
            edited = await self._publisher.edit_message(existing_message_id, text)
        except httpx.HTTPStatusError as e:
            if self._is_gone(e):
                # Single-replacement recovery: exactly one send+pin per
                # call, by construction — there is no loop here. If the
                # replacement itself fails, the exception propagates to
                # the digest service, which isolates it; the persisted
                # state still holds the old id, so the next cycle takes
                # this same single-replacement path again.
                logger.warning(
                    "Digest message gone, creating replacement",
                    old_message_id=existing_message_id,
                )
                return await self._create_and_pin(text)
            raise

        if not edited:
            raise TelegramPublishError("Digest edit returned not-ok")

        # edit_message returns True for both a real edit and Telegram's
        # 400 "message is not modified" retry-idempotence path; the two
        # cannot be told apart here, so ``unchanged`` stays False. The
        # service stores a content fingerprint for observability only and
        # always calls this method — identical content is simply a no-op
        # edit absorbed by that idempotent path.
        logger.info("Digest message edited", message_id=existing_message_id)
        pinned = await self._ensure_pinned(existing_message_id)
        return DigestPublishResult(
            message_id=existing_message_id,
            created=False,
            pinned=pinned,
            unchanged=False,
        )

    async def close(self) -> None:
        if self._owns_publisher:
            await self._publisher.close()

    async def _create_and_pin(self, text: str) -> DigestPublishResult:
        message_id = await self._publisher.publish_message(text)
        if not message_id:
            raise TelegramPublishError("Digest message send returned no message id")
        logger.info("Digest message created", message_id=message_id)
        pinned = await self._pin_best_effort(message_id)
        return DigestPublishResult(
            message_id=message_id, created=True, pinned=pinned, unchanged=False
        )

    async def _ensure_pinned(self, message_id: str) -> bool:
        # getChat exposes only the NEWEST pinned message. Three cases:
        #   - it is ours: already pinned, nothing to do;
        #   - the chat has no pinned message at all: ours was manually
        #     unpinned, so re-pin the same message (silent);
        #   - a DIFFERENT message is the newest pin: do nothing. Ours may
        #     still be pinned lower in the pin list, and another admin's
        #     pin is never fought, covered, or unpinned.
        chat = await self._get_chat_best_effort()
        if chat is None:
            return False

        pinned_message = chat.get("pinned_message")
        if pinned_message is None:
            repinned = await self._pin_best_effort(message_id)
            if repinned:
                logger.info("Digest message re-pinned", message_id=message_id)
            return repinned

        return str(pinned_message.get("message_id")) == str(message_id)

    async def _pin_best_effort(self, message_id: str) -> bool:
        # Pinning is best-effort: a digest that exists but is momentarily
        # unpinned is repaired by the next cycle; pin problems must never
        # fail a publish that already succeeded.
        try:
            return await self._publisher.pin_chat_message(
                message_id, disable_notification=True
            )
        except Exception as e:
            logger.warning(
                "Digest pin failed", message_id=message_id, error=type(e).__name__
            )
            return False

    async def _get_chat_best_effort(self) -> dict | None:
        try:
            return await self._publisher.get_chat()
        except Exception as e:
            logger.warning("Digest getChat failed", error=type(e).__name__)
            return None

    @staticmethod
    def _is_gone(error: httpx.HTTPStatusError) -> bool:
        if error.response.status_code != 400:
            return False
        text = error.response.text.lower()
        return any(marker in text for marker in _GONE_MARKERS)
