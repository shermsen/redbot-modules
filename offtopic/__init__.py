import discord
from .offtopic import OffTopic, offtopic_context_menu

async def setup(bot):
    await bot.add_cog(OffTopic(bot))
    bot.tree.add_command(offtopic_context_menu)

async def teardown(bot):
    bot.tree.remove_command("Off-Topic ab hier", type=discord.AppCommandType.message)
