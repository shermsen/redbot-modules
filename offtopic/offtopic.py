import discord
from redbot.core import Config, checks, commands, app_commands
from redbot.core.bot import Red
from openai import AsyncOpenAI
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List
import asyncio
import json
import logging


class OffTopic(commands.Cog):
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
            "allowed_role_ids": [],
            "vote_timeout": 300,
            "vote_threshold": 5,
            "server_prompt": "This Discord server is about usenet, warez, torrents, automation (Sonarr/Radarr/SABnzbd), indexers, and general IT/piracy topics. Detect when conversations completely derail into unrelated arguments, personal fights, extended off-topic jokes, or random nonsense that has nothing to do with the server's purpose.",
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

    @app_commands.command(name="offtopic", description="Arr! Schaut ob hier wer vom Kurs abgekommen ist")
    @app_commands.guild_only()
    async def offtopic_slash(self, interaction: discord.Interaction):
        """Analyze the last 30 messages for off-topic discussion."""
        await interaction.response.defer()

        guild = interaction.guild
        channel = interaction.channel
        user = interaction.user

        self.log.info(f"/offtopic used by {user} ({user.id}) in #{channel.name} ({channel.id})")

        # Check if user has required role
        allowed_role_ids = await self.config.guild(guild).allowed_role_ids()
        if allowed_role_ids:
            user_role_ids = [r.id for r in user.roles]
            if not any(role_id in user_role_ids for role_id in allowed_role_ids):
                await interaction.followup.send("Arr, du hast keine Berechtigung für diesen Befehl, Landratte!", ephemeral=True)
                return

        # Check configuration
        offtopic_channel_id = await self.config.guild(guild).offtopic_channel_id()
        if not offtopic_channel_id:
            await interaction.followup.send(
                "Blimey! Kein Off-Topic Kanal konfiguriert. Ein Admin muss `!offtopic setchannel #channel` ausführen.",
                ephemeral=True
            )
            return

        offtopic_channel = guild.get_channel(offtopic_channel_id)
        if not offtopic_channel:
            await interaction.followup.send(
                "Der Off-Topic Kanal ist über Bord gegangen! Ein Admin muss ihn neu konfigurieren.",
                ephemeral=True
            )
            return

        # Get server prompt
        server_prompt = await self.config.guild(guild).server_prompt()

        # Check OpenAI API key
        client = await self._get_openai_client()
        if not client:
            await interaction.followup.send(
                "API-Schlüssel fehlt! Ein Admin muss `[p]set api offtopic openai_api_key,KEY` ausführen.",
                ephemeral=True
            )
            return

        # Check TransferChannel cog
        tc_cog = self.bot.get_cog("TransferChannel")
        if not tc_cog:
            await interaction.followup.send(
                "TransferChannel Cog ist nicht installiert - ohne das kann ich keine Nachrichten verschieben!",
                ephemeral=True
            )
            return

        # Fetch recent messages
        messages = await self._fetch_recent_messages(channel)
        if not messages:
            await interaction.followup.send("Keine aktuellen Nachrichten zum Analysieren gefunden (max. 3 Stunden alt).", ephemeral=True)
            return

        # Analyze with OpenAI
        result = await self._analyze_messages(client, messages, server_prompt)
        if result is None:
            await interaction.followup.send("Konnte die Nachrichten nicht analysieren. Versuch's später nochmal!", ephemeral=True)
            return

        first_offtopic_id, reason = result

        if first_offtopic_id is None:
            self.log.info(f"Analysis result: on-topic")
            await interaction.followup.send("Alles klar hier! Keine Off-Topic Diskussion in den letzten 30 Nachrichten gefunden. Weiter so, Matrosen! ⚓")
            return

        self.log.info(f"Analysis result: off-topic starting at message {first_offtopic_id} - {reason}")

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
            f"**🏴‍☠️ Arr, hier ist jemand vom Kurs abgekommen!**\n\n"
            f"Ab dieser Nachricht ging's los:\n\n"
            f"> **{first_offtopic_msg.author.display_name}**: {content_preview}\n\n"
            f"**Grund:** {reason}\n\n"
            f"**{move_count} Nachricht{'en' if move_count != 1 else ''}** würden nach {offtopic_channel.mention} verfrachtet\n\n"
            f"👍 = Ab in die Bilge damit! | 👎 = Lass mal stecken\n"
            f"({vote_threshold} Stimmen nötig, läuft ab in {timeout_minutes} Min.)"
        )

        summary_message = await interaction.followup.send(summary, wait=True)

        # Start voting
        vote_result = await self._handle_voting(
            channel, summary_message, vote_threshold, vote_timeout
        )

        # Base summary without voting instructions
        base_summary = (
            f"**🏴‍☠️ Arr, hier ist jemand vom Kurs abgekommen!**\n\n"
            f"Ab dieser Nachricht ging's los:\n\n"
            f"> **{first_offtopic_msg.author.display_name}**: {content_preview}\n\n"
            f"**Grund:** {reason}\n\n"
        )

        if vote_result == "approve":
            self.log.info(f"Vote passed: approved")
            # Summary message will be deleted with transferred messages, so send new status
            status_msg = await channel.send(f"⏳ Wird nach {offtopic_channel.mention} verfrachtet...")
            # Transfer and delete messages
            result = await self._transfer_messages_from_interaction(
                interaction, first_offtopic_msg, offtopic_channel, tc_cog
            )
            if result:
                count, jump_url = result
                self.log.info(f"Transferred {count} messages to #{offtopic_channel.name}")
                await status_msg.edit(content=f"🏴‍☠️ **{count} Nachrichten nach {offtopic_channel.mention} verschifft!** {jump_url}\n\n🔨 Bleibt beim Thema - sonst geht's über die Planke!")
            else:
                await status_msg.edit(content="❌ Arr, da ist was schiefgelaufen beim Verschieben!")

        elif vote_result == "reject":
            self.log.info(f"Vote passed: rejected")
            await summary_message.edit(content=base_summary + "❌ **Die Crew hat abgestimmt: Bleibt alles hier!**")

        else:  # timeout
            self.log.info(f"Vote timed out")
            await summary_message.edit(content=base_summary + "⏰ **Abstimmung abgelaufen - keinen interessiert's wohl.**")

    # ==================== ADMIN COMMANDS (PREFIX ONLY) ====================

    @commands.group(name="offtopic", invoke_without_command=True)
    @checks.admin_or_permissions(manage_guild=True)
    async def offtopic_admin(self, ctx: commands.Context):
        """Off-topic detection configuration."""
        await ctx.send_help(ctx.command)

    @offtopic_admin.command(name="setchannel")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the destination channel for off-topic messages."""
        await self.config.guild(ctx.guild).offtopic_channel_id.set(channel.id)
        await ctx.send(f"Off-topic destination set to {channel.mention}")
        await ctx.tick()

    @offtopic_admin.command(name="addrole")
    @checks.admin_or_permissions(manage_guild=True)
    async def add_role(self, ctx: commands.Context, role: discord.Role):
        """Add a role that can use the /offtopic command."""
        async with self.config.guild(ctx.guild).allowed_role_ids() as role_ids:
            if role.id not in role_ids:
                role_ids.append(role.id)
        await ctx.send(f"Added {role.mention} to allowed roles.")
        await ctx.tick()

    @offtopic_admin.command(name="removerole")
    @checks.admin_or_permissions(manage_guild=True)
    async def remove_role(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from using the /offtopic command."""
        async with self.config.guild(ctx.guild).allowed_role_ids() as role_ids:
            if role.id in role_ids:
                role_ids.remove(role.id)
                await ctx.send(f"Removed {role.mention} from allowed roles.")
                await ctx.tick()
            else:
                await ctx.send(f"{role.mention} was not in the allowed roles.")

    @offtopic_admin.command(name="clearroles")
    @checks.admin_or_permissions(manage_guild=True)
    async def clear_roles(self, ctx: commands.Context):
        """Clear all role restrictions (allow everyone)."""
        await self.config.guild(ctx.guild).allowed_role_ids.set([])
        await ctx.send("Role restrictions cleared. Everyone can now use /offtopic.")
        await ctx.tick()

    @offtopic_admin.command(name="setprompt")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_prompt(self, ctx: commands.Context):
        """Set the server-wide prompt that describes what this server is about."""
        current = await self.config.guild(ctx.guild).server_prompt()

        await ctx.send(
            f"**Current prompt:**\n> {current}\n\n"
            f"Reply with the new prompt describing what this server is about, "
            f"and what kind of discussions should be considered off-topic.\n\n"
            f"(or 'cancel' to abort)"
        )

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            msg = await self.bot.wait_for('message', check=check, timeout=120)
            if msg.content.lower() == 'cancel':
                await ctx.send("Cancelled.")
                return

            await self.config.guild(ctx.guild).server_prompt.set(msg.content)
            await ctx.send(f"Server prompt updated:\n> {msg.content}")
            await ctx.tick()
        except asyncio.TimeoutError:
            await ctx.send("Timed out. Please try again.")

    @offtopic_admin.command(name="getprompt")
    @checks.admin_or_permissions(manage_guild=True)
    async def get_prompt(self, ctx: commands.Context):
        """View the current server prompt."""
        prompt = await self.config.guild(ctx.guild).server_prompt()
        await ctx.send(f"**Server prompt:**\n> {prompt}")

    @offtopic_admin.command(name="setmodel")
    @checks.is_owner()
    async def set_model(self, ctx: commands.Context, model: str):
        """Set the OpenAI model to use."""
        await self.config.openai_model.set(model)
        await ctx.send(f"OpenAI model set to `{model}`")
        await ctx.tick()

    @offtopic_admin.command(name="setbaseurl")
    @checks.is_owner()
    async def set_base_url(self, ctx: commands.Context, url: str):
        """Set the OpenAI API base URL."""
        await self.config.openai_base_url.set(url)
        self._reset_client()
        await ctx.send(f"OpenAI base URL set to `{url}`")
        await ctx.tick()

    @offtopic_admin.command(name="settings")
    @checks.admin_or_permissions(manage_guild=True)
    async def show_settings(self, ctx: commands.Context):
        """View current off-topic settings."""
        guild_config = await self.config.guild(ctx.guild).all()
        global_config = await self.config.all()

        offtopic_channel = None
        if guild_config["offtopic_channel_id"]:
            offtopic_channel = ctx.guild.get_channel(guild_config["offtopic_channel_id"])

        allowed_roles = []
        for role_id in guild_config["allowed_role_ids"]:
            role = ctx.guild.get_role(role_id)
            if role:
                allowed_roles.append(role)

        server_prompt = guild_config.get("server_prompt", "")
        prompt_preview = server_prompt[:80] + "..." if len(server_prompt) > 80 else server_prompt

        embed = discord.Embed(title="Off-Topic Settings", color=await ctx.embed_color())
        embed.add_field(
            name="Off-Topic Channel",
            value=offtopic_channel.mention if offtopic_channel else "Not set",
            inline=True
        )
        embed.add_field(
            name="Allowed Roles",
            value=", ".join(r.mention for r in allowed_roles) if allowed_roles else "Everyone",
            inline=True
        )
        embed.add_field(
            name="Server Prompt",
            value=prompt_preview or "Not set",
            inline=False
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
        self, client: AsyncOpenAI, messages: List[discord.Message], server_prompt: str
    ) -> Optional[Tuple[Optional[str], str]]:
        """Analyze messages with OpenAI to find off-topic content."""
        model = await self.config.openai_model()

        # Format messages for the prompt
        formatted = []
        for msg in messages:
            content = msg.content.replace('\n', ' ')[:200]
            formatted.append(f"ID: {msg.id} | Author: {msg.author.display_name} | Content: {content}")

        messages_text = "\n".join(formatted)

        system_prompt = f"""Du analysierst Discord-Nachrichten um zu erkennen, wo eine Konversation entgleist ist.

Server-Kontext: {server_prompt}

Du erhältst eine Liste von Nachrichten in chronologischer Reihenfolge.
Finde die ERSTE Nachricht, bei der die Konversation ins Off-Topic abgedriftet ist.
Achte auf: zusammenhangslose Streitereien, persönliche Angriffe, ausufernde Witz-Ketten, random Nonsens, oder Diskussionen die nichts mit dem Server-Thema zu tun haben.

Sei tolerant - kurze Witze oder kleine Abschweifungen sind okay. Nur flaggen wenn die Konversation wirklich entgleist ist.

Antworte NUR mit einem JSON-Objekt in diesem Format:
{{"first_offtopic_id": "message_id", "reason": "kurze Erklärung auf Deutsch warum das Off-Topic ist"}}
Wenn alles in Ordnung ist: {{"first_offtopic_id": null, "reason": "Alles on-topic"}}

WICHTIG: Antworte NUR mit validem JSON, kein anderer Text. Der reason MUSS auf Deutsch sein."""

        user_prompt = f"""Nachrichten (älteste zuerst):
{messages_text}

Finde die ERSTE Nachricht wo die Konversation entgleist ist (falls vorhanden)."""

        self.log.debug(f"OpenAI request - model: {model}")
        self.log.debug(f"System prompt: {system_prompt}")
        self.log.debug(f"User prompt: {user_prompt}")

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
            self.log.debug(f"OpenAI response: {content}")

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

        self.log.info(f"Voting started (threshold: {threshold}, timeout: {timeout}s)")
        end_time = datetime.now(timezone.utc) + timedelta(seconds=timeout)

        while datetime.now(timezone.utc) < end_time:
            try:
                msg = await channel.fetch_message(summary_message.id)
            except discord.NotFound:
                return "timeout"

            thumbs_up = thumbs_down = 0
            for reaction in msg.reactions:
                if str(reaction.emoji) == "\N{THUMBS UP SIGN}":
                    thumbs_up = reaction.count
                elif str(reaction.emoji) == "\N{THUMBS DOWN SIGN}":
                    thumbs_down = reaction.count

            self.log.debug(f"Vote count: {thumbs_up} approve, {thumbs_down} reject")

            if thumbs_up >= threshold:
                return "approve"
            if thumbs_down >= threshold:
                return "reject"

            await asyncio.sleep(5)

        return "timeout"

    async def _transfer_messages_from_interaction(
        self,
        interaction: discord.Interaction,
        first_offtopic_msg: discord.Message,
        destination: discord.TextChannel,
        tc_cog
    ) -> Optional[Tuple[int, str]]:
        """Transfer messages using TransferChannel cog (from slash command)."""
        source = first_offtopic_msg.channel

        try:
            # Collect messages to transfer (we need to count them before transfer)
            messages_to_transfer = [first_offtopic_msg]
            async for msg in source.history(after=first_offtopic_msg, oldest_first=True):
                messages_to_transfer.append(msg)

            count = len(messages_to_transfer)

            # TransferChannel reverses the list, so pass newest-first
            messages_to_transfer.reverse()

            # Use TransferChannel's transfer_messages method directly
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
