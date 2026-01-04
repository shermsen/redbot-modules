import discord
from discord import app_commands
from discord.ext import commands
from redbot.core import Config, checks, commands as red_commands
from redbot.core.bot import Red
from openai import AsyncOpenAI
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List
import asyncio
import json
import logging


class OffTopic(red_commands.Cog):
    """Detects off-topic discussions using AI and moves them to a designated channel."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=847563921000)
        self.log = logging.getLogger("red.offtopic")

        default_global = {
            "openai_model": "gpt-4o",
            "openai_base_url": "https://api.openai.com/v1",
        }

        default_guild = {
            "offtopic_channel_id": None,
            "allowed_role_id": None,
            "vote_timeout": 300,
            "vote_threshold": 5,
            "channel_topics": {},
        }

        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self._client: Optional[AsyncOpenAI] = None

    async def cog_load(self):
        """Called when the cog is loaded."""
        pass

    def cog_unload(self):
        """Cleanup when cog is unloaded."""
        self._client = None

    async def _get_openai_client(self) -> Optional[AsyncOpenAI]:
        """Get or create OpenAI client."""
        if self._client is None:
            api_keys = await self.bot.get_shared_api_tokens("offtopic")
            api_key = api_keys.get("openai_api_key")
            if not api_key:
                return None
            base_url = await self.config.openai_base_url()
            self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return self._client

    def _reset_client(self):
        """Reset client to pick up new config."""
        self._client = None

    # ==================== SLASH COMMAND ====================

    @app_commands.command(name="offtopic", description="Analyze recent messages for off-topic content")
    async def offtopic_slash(self, interaction: discord.Interaction):
        """Analyze the last 30 messages for off-topic discussion."""
        await interaction.response.defer(thinking=True)

        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user

        # Check if user has required role
        allowed_role_id = await self.config.guild(guild).allowed_role_id()
        if allowed_role_id:
            role = guild.get_role(allowed_role_id)
            if role and role not in user.roles:
                await interaction.followup.send(
                    "You don't have permission to use this command.",
                    ephemeral=True
                )
                return

        # Check configuration
        offtopic_channel_id = await self.config.guild(guild).offtopic_channel_id()
        if not offtopic_channel_id:
            await interaction.followup.send(
                "Off-topic channel not configured. Ask an admin to run `!offtopic setchannel #channel`.",
                ephemeral=True
            )
            return

        offtopic_channel = guild.get_channel(offtopic_channel_id)
        if not offtopic_channel:
            await interaction.followup.send(
                "Configured off-topic channel no longer exists. Ask an admin to reconfigure.",
                ephemeral=True
            )
            return

        # Check channel topic
        channel_topics = await self.config.guild(guild).channel_topics()
        channel_topic = channel_topics.get(str(channel.id))
        if not channel_topic:
            await interaction.followup.send(
                f"No topic configured for {channel.mention}. Ask an admin to run `!offtopic settopic`.",
                ephemeral=True
            )
            return

        # Check OpenAI API key
        client = await self._get_openai_client()
        if not client:
            await interaction.followup.send(
                "API key not configured. Ask an admin to run `[p]set api offtopic openai_api_key,YOUR_KEY`.",
                ephemeral=True
            )
            return

        # Check TransferChannel cog
        tc_cog = self.bot.get_cog("TransferChannel")
        if not tc_cog:
            await interaction.followup.send(
                "TransferChannel cog is not installed. This is required for moving messages.",
                ephemeral=True
            )
            return

        # Fetch recent messages
        messages = await self._fetch_recent_messages(channel)
        if not messages:
            await interaction.followup.send(
                "No recent messages found to analyze (within 3 hours).",
                ephemeral=True
            )
            return

        # Analyze with OpenAI
        result = await self._analyze_messages(client, messages, channel_topic)
        if result is None:
            await interaction.followup.send(
                "Failed to analyze messages. Please try again later.",
                ephemeral=True
            )
            return

        first_offtopic_id, reason = result

        if first_offtopic_id is None:
            await interaction.followup.send("No off-topic discussion found in the last 30 messages!")
            return

        # Find the message object
        first_offtopic_msg = None
        for msg in messages:
            if str(msg.id) == str(first_offtopic_id):
                first_offtopic_msg = msg
                break

        if not first_offtopic_msg:
            await interaction.followup.send(
                f"Could not find message with ID {first_offtopic_id}. It may have been deleted.",
                ephemeral=True
            )
            return

        # Count messages to be moved (first + all after)
        messages_to_move = [first_offtopic_msg]
        async for msg in channel.history(after=first_offtopic_msg, oldest_first=True):
            messages_to_move.append(msg)

        move_count = len(messages_to_move)

        # Create summary message
        content_preview = first_offtopic_msg.content[:100]
        if len(first_offtopic_msg.content) > 100:
            content_preview += "..."

        vote_threshold = await self.config.guild(guild).vote_threshold()
        vote_timeout = await self.config.guild(guild).vote_timeout()
        timeout_minutes = vote_timeout // 60

        summary = (
            f"**Off-Topic Detection Results**\n\n"
            f"The conversation went off-topic starting with this message:\n\n"
            f"> **{first_offtopic_msg.author.display_name}**: {content_preview}\n\n"
            f"**Reason:** {reason}\n\n"
            f"This will move **{move_count} message{'s' if move_count != 1 else ''}** to {offtopic_channel.mention}\n\n"
            f"React :thumbsup: to move | React :thumbsdown: to dismiss\n"
            f"({vote_threshold} votes needed, expires in {timeout_minutes} minutes)"
        )

        summary_message = await interaction.followup.send(summary, wait=True)

        # Start voting
        vote_result = await self._handle_voting(
            channel, summary_message, vote_threshold, vote_timeout
        )

        if vote_result == "approve":
            # Transfer and delete messages
            result = await self._transfer_messages(
                interaction, first_offtopic_msg, offtopic_channel, tc_cog
            )
            if result:
                count, jump_url = result
                await channel.send(f"{count} messages moved to {offtopic_channel.mention}: {jump_url}")
            try:
                await summary_message.delete()
            except discord.HTTPException:
                pass

        elif vote_result == "reject":
            try:
                await summary_message.delete()
            except discord.HTTPException:
                pass
            await channel.send("Off-topic report dismissed by vote.", delete_after=10)

        else:  # timeout
            try:
                await summary_message.delete()
            except discord.HTTPException:
                pass

    # ==================== ADMIN COMMANDS ====================

    @red_commands.group(name="offtopic", invoke_without_command=True)
    @checks.admin_or_permissions(manage_guild=True)
    async def offtopic_group(self, ctx: red_commands.Context):
        """Off-topic detection configuration."""
        await ctx.send_help(ctx.command)

    @offtopic_group.command(name="setchannel")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_channel(self, ctx: red_commands.Context, channel: discord.TextChannel):
        """Set the destination channel for off-topic messages."""
        await self.config.guild(ctx.guild).offtopic_channel_id.set(channel.id)
        await ctx.send(f"Off-topic destination set to {channel.mention}")
        await ctx.tick()

    @offtopic_group.command(name="setrole")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_role(self, ctx: red_commands.Context, role: discord.Role):
        """Set which role can use the /offtopic command."""
        await self.config.guild(ctx.guild).allowed_role_id.set(role.id)
        await ctx.send(f"Allowed role set to {role.mention}")
        await ctx.tick()

    @offtopic_group.command(name="settopic")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_topic(self, ctx: red_commands.Context, channel: discord.TextChannel = None):
        """Set a channel's topic description interactively."""
        channel = channel or ctx.channel
        current_topics = await self.config.guild(ctx.guild).channel_topics()
        current = current_topics.get(str(channel.id))

        if current:
            await ctx.send(
                f"Current topic for {channel.mention}:\n> {current}\n\n"
                f"Reply with the new topic description (or 'cancel' to abort):"
            )
        else:
            await ctx.send(
                f"Setting topic for {channel.mention}\n\n"
                f"**What is this channel about?**\n"
                f"Example: 'Technical discussions about Python and coding projects'\n\n"
                f"Reply with the topic description:"
            )

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            msg = await self.bot.wait_for('message', check=check, timeout=60)
            if msg.content.lower() == 'cancel':
                await ctx.send("Cancelled.")
                return

            current_topics[str(channel.id)] = msg.content
            await self.config.guild(ctx.guild).channel_topics.set(current_topics)
            await ctx.send(f"Topic set for {channel.mention}:\n> {msg.content}")
            await ctx.tick()
        except asyncio.TimeoutError:
            await ctx.send("Timed out. Please try again.")

    @offtopic_group.command(name="gettopic")
    @checks.admin_or_permissions(manage_guild=True)
    async def get_topic(self, ctx: red_commands.Context, channel: discord.TextChannel = None):
        """View a channel's configured topic."""
        channel = channel or ctx.channel
        current_topics = await self.config.guild(ctx.guild).channel_topics()
        topic = current_topics.get(str(channel.id))

        if topic:
            await ctx.send(f"Topic for {channel.mention}:\n> {topic}")
        else:
            await ctx.send(f"No topic configured for {channel.mention}.")

    @offtopic_group.command(name="listtopics")
    @checks.admin_or_permissions(manage_guild=True)
    async def list_topics(self, ctx: red_commands.Context):
        """List all configured channel topics."""
        current_topics = await self.config.guild(ctx.guild).channel_topics()

        if not current_topics:
            await ctx.send("No channel topics configured.")
            return

        lines = []
        for channel_id, topic in current_topics.items():
            channel = ctx.guild.get_channel(int(channel_id))
            if channel:
                preview = topic[:50] + "..." if len(topic) > 50 else topic
                lines.append(f"{channel.mention}: {preview}")
            else:
                lines.append(f"<deleted channel {channel_id}>: {topic[:50]}...")

        await ctx.send("**Configured Topics:**\n" + "\n".join(lines))

    @offtopic_group.command(name="removetopic")
    @checks.admin_or_permissions(manage_guild=True)
    async def remove_topic(self, ctx: red_commands.Context, channel: discord.TextChannel):
        """Remove a channel's topic configuration."""
        current_topics = await self.config.guild(ctx.guild).channel_topics()

        if str(channel.id) in current_topics:
            del current_topics[str(channel.id)]
            await self.config.guild(ctx.guild).channel_topics.set(current_topics)
            await ctx.send(f"Topic removed for {channel.mention}.")
            await ctx.tick()
        else:
            await ctx.send(f"No topic was configured for {channel.mention}.")

    @offtopic_group.command(name="setmodel")
    @checks.is_owner()
    async def set_model(self, ctx: red_commands.Context, model: str):
        """Set the OpenAI model to use."""
        await self.config.openai_model.set(model)
        await ctx.send(f"OpenAI model set to `{model}`")
        await ctx.tick()

    @offtopic_group.command(name="setbaseurl")
    @checks.is_owner()
    async def set_base_url(self, ctx: red_commands.Context, url: str):
        """Set the OpenAI API base URL."""
        await self.config.openai_base_url.set(url)
        self._reset_client()
        await ctx.send(f"OpenAI base URL set to `{url}`")
        await ctx.tick()

    @offtopic_group.command(name="settings")
    @checks.admin_or_permissions(manage_guild=True)
    async def show_settings(self, ctx: red_commands.Context):
        """View current off-topic settings."""
        guild_config = await self.config.guild(ctx.guild).all()
        global_config = await self.config.all()

        offtopic_channel = None
        if guild_config["offtopic_channel_id"]:
            offtopic_channel = ctx.guild.get_channel(guild_config["offtopic_channel_id"])

        allowed_role = None
        if guild_config["allowed_role_id"]:
            allowed_role = ctx.guild.get_role(guild_config["allowed_role_id"])

        topic_count = len(guild_config["channel_topics"])

        embed = discord.Embed(title="Off-Topic Settings", color=await ctx.embed_color())
        embed.add_field(
            name="Off-Topic Channel",
            value=offtopic_channel.mention if offtopic_channel else "Not set",
            inline=True
        )
        embed.add_field(
            name="Allowed Role",
            value=allowed_role.mention if allowed_role else "Everyone",
            inline=True
        )
        embed.add_field(
            name="Configured Topics",
            value=f"{topic_count} channel(s)",
            inline=True
        )
        embed.add_field(
            name="Vote Threshold",
            value=str(guild_config["vote_threshold"]),
            inline=True
        )
        embed.add_field(
            name="Vote Timeout",
            value=f"{guild_config['vote_timeout'] // 60} minutes",
            inline=True
        )
        embed.add_field(
            name="OpenAI Model",
            value=f"`{global_config['openai_model']}`",
            inline=True
        )

        # Check API key status
        api_keys = await self.bot.get_shared_api_tokens("offtopic")
        api_status = "Configured" if api_keys.get("openai_api_key") else "Not set"
        embed.add_field(name="API Key", value=api_status, inline=True)

        # Check TransferChannel
        tc_status = "Installed" if self.bot.get_cog("TransferChannel") else "Not installed"
        embed.add_field(name="TransferChannel", value=tc_status, inline=True)

        await ctx.send(embed=embed)

    # ==================== HELPER METHODS ====================

    async def _fetch_recent_messages(
        self, channel: discord.TextChannel, limit: int = 30, max_age_hours: int = 3
    ) -> List[discord.Message]:
        """Fetch recent human messages from the channel."""
        messages = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)

        async for msg in channel.history(limit=100):  # Fetch more to account for bot messages
            if msg.author.bot:
                continue
            if msg.created_at >= cutoff:
                messages.append(msg)
                if len(messages) >= limit:
                    break

        # Return in chronological order (oldest first)
        return list(reversed(messages))

    async def _analyze_messages(
        self, client: AsyncOpenAI, messages: List[discord.Message], channel_topic: str
    ) -> Optional[Tuple[Optional[str], str]]:
        """Analyze messages with OpenAI to find off-topic content."""
        model = await self.config.openai_model()

        # Format messages for the prompt
        formatted = []
        for msg in messages:
            content = msg.content.replace('\n', ' ')[:200]
            formatted.append(f"ID: {msg.id} | Author: {msg.author.display_name} | Content: {content}")

        messages_text = "\n".join(formatted)

        system_prompt = f"""You analyze Discord messages to identify where a conversation went off-topic.
The channel's topic is: {channel_topic}

You will receive a list of messages in chronological order.
Find the FIRST message where the conversation started going off-topic.
Return ONLY a JSON object with this structure:
{{"first_offtopic_id": "message_id", "reason": "brief explanation of why this derailed the topic"}}
If all messages are on-topic, return: {{"first_offtopic_id": null, "reason": "All messages are on-topic"}}

IMPORTANT: Return ONLY valid JSON, no other text."""

        user_prompt = f"""Messages (oldest first):
{messages_text}

Find the FIRST message where the conversation went off-topic for a channel about: {channel_topic}"""

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=200,
                temperature=0.3
            )

            content = response.choices[0].message.content.strip()

            # Parse JSON response
            # Try to extract JSON if wrapped in markdown
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            result = json.loads(content)
            return result.get("first_offtopic_id"), result.get("reason", "")

        except json.JSONDecodeError as e:
            self.log.error(f"Failed to parse OpenAI response as JSON: {e}")
            self.log.error(f"Response was: {content}")
            return None
        except Exception as e:
            self.log.error(f"OpenAI API error: {e}")
            return None

    async def _handle_voting(
        self,
        channel: discord.TextChannel,
        summary_message: discord.Message,
        threshold: int,
        timeout: int
    ) -> str:
        """Handle the voting process. Returns 'approve', 'reject', or 'timeout'."""
        await summary_message.add_reaction("\N{THUMBS UP SIGN}")
        await summary_message.add_reaction("\N{THUMBS DOWN SIGN}")

        end_time = datetime.now(timezone.utc) + timedelta(seconds=timeout)

        while datetime.now(timezone.utc) < end_time:
            try:
                msg = await channel.fetch_message(summary_message.id)
            except discord.NotFound:
                return "timeout"

            thumbs_up = thumbs_down = 0
            for reaction in msg.reactions:
                if str(reaction.emoji) == "\N{THUMBS UP SIGN}":
                    thumbs_up = reaction.count - 1  # Subtract bot's reaction
                elif str(reaction.emoji) == "\N{THUMBS DOWN SIGN}":
                    thumbs_down = reaction.count - 1

            if thumbs_up >= threshold:
                return "approve"
            if thumbs_down >= threshold:
                return "reject"

            await asyncio.sleep(5)

        return "timeout"

    async def _transfer_messages(
        self,
        interaction: discord.Interaction,
        first_offtopic_msg: discord.Message,
        destination: discord.TextChannel,
        tc_cog
    ) -> Optional[Tuple[int, str]]:
        """Transfer messages using TransferChannel cog."""
        source = first_offtopic_msg.channel

        try:
            # Collect messages to transfer (we need to count them before transfer)
            messages_to_transfer = [first_offtopic_msg]
            async for msg in source.history(after=first_offtopic_msg, oldest_first=True):
                messages_to_transfer.append(msg)

            count = len(messages_to_transfer)

            # Use TransferChannel's transfer_messages method directly
            # This is cleaner than ctx.invoke for programmatic use

            # Transfer messages - pass the list directly
            await tc_cog.transfer_messages(
                await self.bot.get_context(first_offtopic_msg),
                source=source,
                destination=destination,
                way="webhooks",
                messages=messages_to_transfer
            )

            # Delete originals
            for msg in messages_to_transfer:
                try:
                    await msg.delete()
                except discord.HTTPException:
                    pass

            # Get jump URL to first message in destination
            jump_url = ""
            async for msg in destination.history(limit=count):
                jump_url = msg.jump_url

            return count, jump_url

        except Exception as e:
            self.log.error(f"Transfer error: {e}")
            await interaction.channel.send(f"Error transferring messages: {e}")
            return None
