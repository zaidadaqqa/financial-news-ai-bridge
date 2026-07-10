import asyncio
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from sqlalchemy import select

from app.config.settings import settings
from app.database.connection import AsyncSessionLocal
from app.log.logger import get_logger
from app.models.news import NewsEvent
from app.services.news.orchestrator import NewsOrchestrator

logger = get_logger(__name__)

_FJ_PREFIX = "FinancialJuice: "

# Conservative headers — avoid triggering WAF rate limits
_HEADERS = {
    "User-Agent": "FinancialBridge/2.0 RSS Reader",
    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
}


class RSSPoller:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._seen_ids: set[str] = set()
        self._running = False
        self._etag: str | None = None
        self._last_modified: str | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
        )
        await self._seed_seen_ids()
        self._running = True
        logger.info(
            "RSS poller started",
            url=settings.FJ_RSS_URL,
            interval_seconds=settings.RSS_POLL_INTERVAL,
            seeded_ids=len(self._seen_ids),
        )

        backoff = 0
        while self._running:
            if backoff > 0:
                await asyncio.sleep(backoff)
                backoff = 0
            try:
                await self._poll_once()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    backoff = 120
                    logger.warning(
                        "RSS: rate limited (429), backing off",
                        backoff_seconds=backoff,
                    )
                else:
                    logger.error(
                        "RSS: HTTP error",
                        status=e.response.status_code,
                        error_type=type(e).__name__,
                    )
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.warning(
                    "RSS: connection error, will retry next interval",
                    error_type=type(e).__name__,
                    detail=str(e)[:80],
                )
            except ET.ParseError as e:
                logger.error("RSS: XML parse error", detail=str(e)[:120])
            except Exception as e:
                logger.error(
                    "RSS: unexpected error",
                    error_type=type(e).__name__,
                    detail=str(e)[:120],
                )
            await asyncio.sleep(settings.RSS_POLL_INTERVAL)

    async def _initial_feed_scan(self) -> None:
        """On first run (empty DB), silently mark all current feed items as seen.

        Prevents flooding Telegram with historical news on the first startup.
        """
        assert self._client is not None
        try:
            response = await self._client.get(settings.FJ_RSS_URL)
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                channel = root.find("channel")
                if channel is not None:
                    for item in channel.findall("item"):
                        guid = (item.findtext("guid") or "").strip()
                        if guid:
                            self._seen_ids.add(guid)
                    self._etag = response.headers.get("ETag")
                    self._last_modified = response.headers.get("Last-Modified")
        except Exception as e:
            logger.warning(
                "RSS: initial feed scan failed, starting fresh",
                error_type=type(e).__name__,
                detail=str(e)[:80],
            )
        logger.info(
            "RSS: initial scan complete — existing items marked as seen, "
            "will only publish new items going forward",
            seen_count=len(self._seen_ids),
        )

    async def close(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()

    async def _seed_seen_ids(self) -> None:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(NewsEvent.source_message_id))
            self._seen_ids = set(result.scalars().all())

        if self._seen_ids:
            logger.info(
                "RSS poller seeded seen IDs from database", count=len(self._seen_ids)
            )
        else:
            logger.info(
                "RSS poller: empty database detected — performing initial feed scan "
                "to avoid posting historical items"
            )
            await self._initial_feed_scan()

    async def _poll_once(self) -> None:
        assert self._client is not None

        req_headers: dict[str, str] = {}
        if self._etag:
            req_headers["If-None-Match"] = self._etag
        if self._last_modified:
            req_headers["If-Modified-Since"] = self._last_modified

        response = await self._client.get(settings.FJ_RSS_URL, headers=req_headers)
        response.raise_for_status()

        if response.status_code == 304:
            return  # Feed unchanged since last poll

        self._etag = response.headers.get("ETag")
        self._last_modified = response.headers.get("Last-Modified")

        new_items = self._parse_feed(response.content)
        if not new_items:
            return

        logger.info("RSS poll: new items found", count=len(new_items))

        for idx, item in enumerate(new_items):
            self._seen_ids.add(item["guid"])
            title = item["title"]
            if not title:
                continue
            async with AsyncSessionLocal() as session:
                orch = NewsOrchestrator(session)
                await orch.process_message(
                    source_id=item["guid"],
                    source="rss",
                    headline=title,
                    source_url=item["link"],
                )
            # Avoid flooding Telegram when multiple items arrive in one poll
            if idx < len(new_items) - 1:
                await asyncio.sleep(3)

    def _parse_feed(self, content: bytes) -> list[dict]:
        root = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            logger.warning("RSS: no <channel> element found")
            return []

        new_items: list[dict] = []
        for item in channel.findall("item"):
            guid = (item.findtext("guid") or "").strip()
            if not guid or guid in self._seen_ids:
                continue

            raw_title = (item.findtext("title") or "").strip()
            title = raw_title.removeprefix(_FJ_PREFIX).strip()

            link = (item.findtext("link") or "").strip() or None
            pub_date_str = item.findtext("pubDate") or ""
            try:
                pub_date = parsedate_to_datetime(pub_date_str)
            except Exception:
                pub_date = datetime.now(UTC)

            new_items.append(
                {"guid": guid, "title": title, "link": link, "pub_date": pub_date}
            )

        # Process oldest-first for consistent ordering
        new_items.sort(key=lambda x: x["pub_date"])
        return new_items
