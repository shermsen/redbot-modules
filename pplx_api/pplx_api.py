import discord
from discord import Message, ui, ButtonStyle
from redbot.core import Config, checks, commands
from typing import List, Dict, Optional
import openai
from openai import AsyncOpenAI
import asyncio
import re
import aiohttp
import logging
from datetime import datetime


class PerplexityAI(commands.Cog):
    """Send messages to Perplexity AI"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=359554900000)
        default_global = {
            "perplexity_api_key": None,
            "perplexity_api_key_2": None,
            "perplexity_api_key_3": None,
            "model": "sonar-reasoning-pro",
            "max_tokens": 8000,
            "prompt": "",
        }
        self.config.register_global(**default_global)
        self.log = logging.getLogger("red.pplx_api")
        self._clients = {}  # Cache clients by API key
        self._upload_tasks = set()  # Track upload tasks for cleanup

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        # Cancel any running upload tasks
        for task in self._upload_tasks:
            if not task.done():
                task.cancel()
        # Clear client cache
        self._clients.clear()

    async def perplexity_api_keys(self):
        return await self.bot.get_shared_api_tokens("perplexity")

    async def upload_to_0x0(self, text: str) -> str:
        """Upload text to 0x0.at file sharing service"""
        url = "https://x0.at"
        data = aiohttp.FormData()
        data.add_field('file', text, filename='thinking.txt')
        data.add_field('secret', '')
        
        timeout = aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=data) as response:
                    if response.status == 200:
                        return (await response.text()).strip()
                    else:
                        raise Exception(f"Upload failed: HTTP {response.status}")
        except asyncio.TimeoutError:
            raise Exception("Upload timeout after 30 seconds")
        except Exception as e:
            raise Exception(f"Upload error: {str(e)}")

    @commands.command(aliases=['pplx'])
    async def perplexity(self, ctx: commands.Context, *, message: str = ""):
        """Send a message to Perplexity AI, combining referenced message and additional text."""
        question = await self._get_question(ctx, message)
        if not question:
            await ctx.send("❓ Please provide a question either as text or by replying to a message.")
            return

        await self.do_perplexity(ctx, question)

    @commands.command(aliases=['pplxdeep'])
    async def perplexitydeep(self, ctx: commands.Context, *, message: str = ""):
        """Send a message to Perplexity AI using the sonar-deep-research model for more thorough responses."""
        question = await self._get_question(ctx, message)
        if not question:
            await ctx.send("Please provide a question either as text or by replying to a message.")
            return

        await self.do_perplexity(ctx, question, model="sonar-deep-research")

    @commands.command(aliases=['r1'])
    async def r1model(self, ctx: commands.Context, *, message: str = ""):
        """Send a message to Perplexity AI using the r1-1776 model."""
        question = await self._get_question(ctx, message)
        if not question:
            await ctx.send("Please provide a question either as text or by replying to a message.")
            return

        await self.do_perplexity(ctx, question, model="r1-1776")

    async def _get_question(self, ctx: commands.Context, message: str = "") -> str:
        """Extract question from reference and additional text."""
        question = ""

        # Check if the command is invoked as a reply
        if ctx.message.reference:
            ref = ctx.message.reference
            try:
                referenced_msg = ref.resolved or await ctx.channel.fetch_message(ref.message_id)
            except discord.NotFound:
                await ctx.send("❌ The referenced message could not be found.")
                return ""
            except discord.Forbidden:
                await ctx.send("❌ I don't have permission to access that message.")
                return ""
            except discord.HTTPException as e:
                self.log.error(f"Discord API error fetching message: {e}")
                await ctx.send(f"❌ An error occurred accessing the message: {str(e)}")
                return ""

            # Validate referenced message
            if isinstance(referenced_msg, discord.DeletedReferencedMessage):
                await ctx.send("❌ The referenced message was deleted.")
                return ""
            if not isinstance(referenced_msg, discord.Message) or not referenced_msg.content.strip():
                await ctx.send("❌ The referenced message has no text content.")
                return ""

            question = referenced_msg.content.strip()

        # Combine with additional text if provided
        additional_text = message.strip()
        if additional_text:
            question = f"{question} {additional_text}" if question else additional_text

        return question

    async def do_perplexity(self, ctx: commands.Context, message: str, model: str = None):
        async with ctx.typing():
            # Validate API keys
            api_keys = (await self.perplexity_api_keys()).values()
            if not any(api_keys):
                prefix = ctx.prefix if ctx.prefix else "[p]"
                return await ctx.send(f"API keys missing! Use `{prefix}set api perplexity api_key,api_key_2,api_key_3`")

            # Get configuration
            model, max_tokens = await self._get_model_config(model)
            messages = await self._prepare_messages(message)

            # Call API
            try:
                response = await self.call_api(model, api_keys, messages, max_tokens)
                if not response:
                    return await ctx.send("❌ No response from API - all keys may be invalid or rate limited")

                # Process response
                await self._process_and_send_response(ctx, response)
            except Exception as e:
                self.log.error(f"Error in do_perplexity: {e}")
                await ctx.send(f"❌ An error occurred: {str(e)}")

    async def _process_and_send_response(self, ctx: commands.Context, response):
        """Process API response and send to Discord"""
        try:
            content = response.choices[0].message.content
            if not content:
                await ctx.send("❌ Received empty response from API")
                return
                
            search_results = self._extract_search_results(response)

            # Handle reasoning content
            upload_url = await self._handle_reasoning_content(content)
            if upload_url:
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)

            # Split and send content
            chunks = self.smart_split(content)
            if not chunks:
                await ctx.send("❌ No content to send after processing")
                return
                
            for index, chunk in enumerate(chunks):
                view = None
                if index == len(chunks) - 1 and upload_url:
                    view = self.create_view(upload_url, ctx.guild)
                await ctx.send(chunk, view=view)
                await ctx.typing()
                await asyncio.sleep(0.5)

            # Send citations
            citation_lines = self._format_search_results(search_results)
            if citation_lines:
                header = "**Quellen:**"
                full_message = f"{header}\n" + "\n".join(citation_lines)
                await ctx.send(full_message)
        except Exception as e:
            self.log.error(f"Error processing response: {e}")
            await ctx.send(f"❌ Error processing response: {str(e)}")

    def create_view(self, upload_url, guild):
        """Helper to create a view with the reasoning button."""
        bigbrain_emoji = discord.utils.get(guild.emojis, name="bigbrain") if guild else None
        view = ui.View()
        button = ui.Button(
            style=ButtonStyle.primary,
            label="Reasoning",
            url=upload_url,
            emoji=bigbrain_emoji or "🧠"
        )
        view.add_item(button)
        return view

    def _get_or_create_client(self, api_key: str) -> AsyncOpenAI:
        """Get or create cached API client"""
        if api_key not in self._clients:
            self._clients[api_key] = AsyncOpenAI(
                api_key=api_key, 
                base_url="https://api.perplexity.ai"
            )
        return self._clients[api_key]

    def _extract_search_results(self, response) -> List[Dict[str, str]]:
        """Extract search results from API response"""
        try:
            # Try new search_results format first
            if hasattr(response, 'search_results') and response.search_results:
                return response.search_results
            # Fallback to deprecated citations for backward compatibility
            elif hasattr(response, 'citations') and response.citations:
                return [{"url": url, "title": "", "date": ""} for url in response.citations]
            return []
        except Exception as e:
            self.log.warning(f"Error extracting search results: {e}")
            return []

    def _format_search_results(self, search_results: List[Dict[str, str]]) -> List[str]:
        """Format search results for display"""
        if not search_results:
            return []
        
        formatted = []
        for i, result in enumerate(search_results, 1):
            url = result.get('url', '')
            title = result.get('title', '')
            date = result.get('date', '')
            
            if title and date:
                # Format: "Title" (Date) - <URL>
                try:
                    parsed_date = datetime.fromisoformat(date.replace('Z', '+00:00'))
                    date_str = parsed_date.strftime('%Y-%m-%d')
                    formatted.append(f"{i}. \"{title}\" ({date_str}) - <{url}>")
                except (ValueError, AttributeError):
                    formatted.append(f"{i}. \"{title}\" - <{url}>")
            elif title:
                # Format: "Title" - <URL>
                formatted.append(f"{i}. \"{title}\" - <{url}>")
            elif url:
                # Fallback: just URL
                formatted.append(f"{i}. <{url}>")
        
        return formatted

    async def _handle_reasoning_content(self, content: str) -> Optional[str]:
        """Extract and upload reasoning content, return URL if successful"""
        think_match = re.search(r'<think>(.*?)</think>', content, re.DOTALL)
        if not think_match:
            return None
        
        think_text = think_match.group(1)
        try:
            task = asyncio.create_task(self.upload_to_0x0(think_text))
            self._upload_tasks.add(task)
            upload_url = await task
            self._upload_tasks.discard(task)
            return upload_url
        except Exception as e:
            self.log.warning(f"Failed to upload reasoning: {e}")
            return None

    async def _prepare_messages(self, message: str) -> List[Dict[str, str]]:
        """Prepare messages array with optional system prompt"""
        messages = [{"role": "user", "content": message}]
        if prompt := await self.config.prompt():
            messages.insert(0, {"role": "system", "content": prompt})
        return messages

    async def _get_model_config(self, override_model: Optional[str] = None) -> tuple[str, int]:
        """Get model and max_tokens configuration"""
        if override_model:
            model = override_model
        else:
            model = await self.config.model()
        max_tokens = await self.config.max_tokens() or 8000
        return model, max_tokens

    async def call_api(self, model: str, api_keys: list, messages: List[dict], max_tokens: int):
        for key in filter(None, api_keys):
            try:
                client = self._get_or_create_client(key)
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    web_search_options={"search_context_size": "high"}
                )
                return response
            except Exception as e:
                self.log.error(f"API Error with key {key[:8]}...: {str(e)}")
        self.log.error("All API keys failed")
        return None

    def smart_split(self, text: str, limit: int = 1950) -> List[str]:
        chunks = []
        current_chunk = []
        current_length = 0
        in_code_block = False

        lines = text.split('\n')
        for line in lines:
            line_stripped = line.strip()

            # Toggle code block state on lines with ```
            if line_stripped.startswith('```'):
                in_code_block = not in_code_block

            new_length = current_length + len(line) + 1  # +1 for newline

            if new_length > limit:
                # Finalize current chunk
                chunk = '\n'.join(current_chunk)

                # Add code block closure if needed
                if in_code_block:
                    chunk += '\n```'
                    # Next chunk should start with code block opener
                    chunks.append(chunk)
                    current_chunk = ['```', line]
                    current_length = len('```')
                else:
                    chunks.append(chunk)
                    current_chunk = [line]
                    current_length = len(line) + 1
            else:
                current_chunk.append(line)
                current_length = new_length

        # Add remaining content
        if current_chunk:
            chunk = '\n'.join(current_chunk)
            if in_code_block:
                chunk += '\n```'
            chunks.append(chunk)

        return chunks

    @commands.command()
    @checks.is_owner()
    async def setperplexitytokens(self, ctx: commands.Context, tokens: int):
        """Set max tokens (400-8000 range)"""
        clamped_tokens = max(400, min(tokens, 8000))
        await self.config.max_tokens.set(clamped_tokens)
        await ctx.send(f"Max tokens set to {clamped_tokens}")
        await ctx.tick()

    @commands.command()
    @checks.is_owner()
    async def getperplexitymodel(self, ctx: commands.Context):
        """Get the model for Perplexity AI."""
        model = await self.config.model()
        await ctx.send(f"Perplexity AI model set to `{model}`")

    @commands.command()
    @checks.is_owner()
    async def setperplexitymodel(self, ctx: commands.Context, model: str):
        """Set the model for Perplexity AI."""
        await self.config.model.set(model)
        await ctx.send("Perplexity AI model set.")

    @commands.command()
    @checks.is_owner()
    async def getperplexitytokens(self, ctx: commands.Context):
        """Get the maximum number of tokens for Perplexity AI to generate."""
        tokens = await self.config.max_tokens()
        await ctx.send(f"Perplexity AI maximum number of tokens set to `{tokens}`")

    @commands.command()
    @checks.is_owner()
    async def getperplexityprompt(self, ctx: commands.Context):
        """Get the prompt for Perplexity AI."""
        prompt = await self.config.prompt()
        await ctx.send(f"Perplexity AI prompt is set to: `{prompt}`")

    @commands.command()
    @checks.is_owner()
    async def setperplexityprompt(self, ctx: commands.Context, *, prompt: str):
        """Set the prompt for Perplexity AI."""
        await self.config.prompt.set(prompt)
        await ctx.send("Perplexity AI prompt set.")


def setup(bot):
    bot.add_cog(PerplexityAI(bot))
