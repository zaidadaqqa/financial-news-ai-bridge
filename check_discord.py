import asyncio

import discord

from app.config.settings import settings


async def main():
    token = settings.DISCORD_BOT_TOKEN.get_secret_value()
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Discord logged in as {client.user}")
        guild = client.get_guild(int(settings.DISCORD_GUILD_ID))
        if guild:
            print("Verified guild access.")
            channel = guild.get_channel(int(settings.DISCORD_SOURCE_CHANNEL_ID))
            if channel:
                print("Verified source channel access.")
                perms = channel.permissions_for(guild.me)
                if perms.read_messages:
                    print("Verified read permissions.")
                else:
                    print("Missing read permissions.")
            else:
                print("Source channel not found.")
        else:
            print("Guild not found.")
        print("Waiting for new Discord messages...")

    try:
        await client.start(token)
    except discord.LoginFailure:
        print("Discord LoginFailure: Improper token has been passed.")
    except Exception as e:
        print(f"Discord error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
