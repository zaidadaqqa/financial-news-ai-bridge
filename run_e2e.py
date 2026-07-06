import asyncio

from app.database.connection import AsyncSessionLocal
from app.services.news.orchestrator import NewsOrchestrator


async def main():
    async with AsyncSessionLocal() as session:
        orchestrator = NewsOrchestrator(session)
        print("Starting live E2E test...")
        await orchestrator.process_discord_message(
            message_id="live_msg_12345",
            channel_id="620632187246739457",
            headline="US Federal Reserve cuts interest rates by 25 basis points.",
            source_url="https://financialjuice.com",
        )
        print("Waiting 15 seconds for background pipeline to complete...")
        await asyncio.sleep(15)
        print("Live E2E test completed.")


if __name__ == "__main__":
    asyncio.run(main())
