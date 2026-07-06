import discord

from app.config.settings import settings
from app.database.connection import AsyncSessionLocal
from app.log.logger import get_logger
from app.services.news.orchestrator import NewsOrchestrator

logger = get_logger(__name__)


class BridgeDiscordClient(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.source_channel_id = settings.DISCORD_SOURCE_CHANNEL_ID
        self.guild_id = settings.DISCORD_GUILD_ID

    async def on_ready(self) -> None:
        logger.info(f"Discord Bot connected as {self.user}")

        # Verification Checks
        guild = self.get_guild(self.guild_id)
        if not guild:
            logger.error(
                "Discord Error: Guild not found or bot not invited to the guild.",
                guild_id=self.guild_id,
            )
            return

        channel = guild.get_channel(self.source_channel_id)
        if not channel:
            logger.error(
                "Discord Error: Source channel not found in the guild.",
                channel_id=self.source_channel_id,
            )
            return

        perms = channel.permissions_for(guild.me)
        missing_perms = []
        if not perms.view_channel:
            missing_perms.append("View Channel")
        if not perms.read_messages:
            missing_perms.append("Read Messages")
        if not perms.read_message_history:
            missing_perms.append("Read Message History")

        if missing_perms:
            logger.error(
                "Discord Error: Missing required permissions in the source channel.",
                missing=missing_perms,
            )
            return

        logger.info(
            "Discord pre-flight checks passed. "
            "Listening for real FinancialJuice messages..."
        )

    async def on_disconnect(self) -> None:
        logger.warning("Discord Bot disconnected. Attempting to reconnect...")

    async def on_resumed(self) -> None:
        logger.info("Discord Bot successfully reconnected and resumed session.")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author == self.user:
            return

        if message.channel.id != self.source_channel_id:
            return

        headline = message.content.strip()
        if not headline and message.embeds:
            embed = message.embeds[0]
            headline = embed.title or embed.description or ""

        source_url = None
        if message.embeds and message.embeds[0].url:
            source_url = message.embeds[0].url

        if not headline:
            logger.debug("Ignored empty message", message_id=str(message.id))
            return

        try:
            async with AsyncSessionLocal() as session:
                orchestrator = NewsOrchestrator(session)
                await orchestrator.process_discord_message(
                    message_id=str(message.id),
                    channel_id=str(message.channel.id),
                    headline=headline,
                    source_url=source_url,
                )
        except Exception as e:
            logger.error("Failed to handle Discord message", error=str(e))

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        try:
            await super().start(token, reconnect=reconnect)
        except discord.LoginFailure:
            logger.error(
                "Discord LoginFailure: Improper token has been passed. "
                "Check DISCORD_BOT_TOKEN."
            )
        except discord.PrivilegedIntentsRequired:
            logger.error(
                "Discord Error: Message Content Intent is not enabled "
                "in the Developer Portal."
            )
        except Exception as e:
            logger.error("Discord client encountered a fatal error.", error=str(e))
