import asyncio

import httpx

from app.config.settings import settings


async def main():
    tg_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    chat_id = settings.TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{tg_token}/sendMessage"

    msg = (
        "✅ Financial News AI Bridge is online.\n"
        "Telegram: connected\n"
        "Database: connected\n"
        "AI: connected / failed\n"
        "Discord: connected / failed"
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.post(url, json={"chat_id": chat_id, "text": msg})
            if resp.status_code != 200:
                print(f"FAILED: Status {resp.status_code}, Body: {resp.text}")
            else:
                print("SUCCESS")
    except Exception as e:
        print(f"NETWORK ERROR: {type(e).__name__} - {e}")


if __name__ == "__main__":
    asyncio.run(main())
