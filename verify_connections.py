import asyncio

import httpx

from app.config.settings import settings
from app.database.connection import engine
from app.services.ai.openai_provider import OpenAIProvider


async def check_discord():
    token = settings.DISCORD_BOT_TOKEN.get_secret_value()
    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            r = await c.get("https://discord.com/api/v10/users/@me", headers=headers)
            return r.status_code == 200
        except httpx.HTTPError:
            return False


async def main():
    db_connected = False
    try:
        async with engine.connect():
            db_connected = True
    except Exception as e:
        print(f"Database failed: {e}")

    ai_connected = False
    try:
        ai_provider = OpenAIProvider()
        # Direct raw call to test auth
        async with httpx.AsyncClient(timeout=30.0) as c:
            headers = {"Authorization": f"Bearer {ai_provider.api_key}"}
            r = await c.get(f"{ai_provider.base_url}/models", headers=headers)
            ai_connected = r.status_code == 200
    except Exception as e:
        print(f"AI failed: {e}")

    discord_connected = await check_discord()

    # Telegram Check
    tg_token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    chat_id = settings.TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{tg_token}/sendMessage"

    msg = (
        "✅ Financial News AI Bridge is online.\n"
        "Telegram: connected\n"
        f"Database: {'connected' if db_connected else 'failed'}\n"
        f"AI: {'connected' if ai_connected else 'failed'}\n"
        f"Discord: {'connected' if discord_connected else 'failed'}"
    )

    async with httpx.AsyncClient(timeout=30.0) as c:
        resp = await c.post(url, json={"chat_id": chat_id, "text": msg})
        if resp.status_code != 200:
            data = resp.json()
            desc = data.get("description", "")
            if resp.status_code == 401:
                print("Telegram verification failed: Token invalid.")
            elif resp.status_code == 400 and "chat not found" in desc.lower():
                print(
                    "Telegram verification failed: chat not found "
                    "(channel username is wrong)."
                )
            elif (
                resp.status_code == 403
                or "admin" in desc.lower()
                or "rights" in desc.lower()
            ):
                print(
                    "Telegram verification failed: bot is not admin or "
                    "Post Messages permission missing."
                )
            else:
                print(f"Telegram verification failed: {desc}")
            return
        else:
            print("Telegram verification successful.")


if __name__ == "__main__":
    asyncio.run(main())
