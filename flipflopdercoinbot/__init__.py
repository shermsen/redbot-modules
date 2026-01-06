from .flipflopdercoinbot import FlipFlopDerCoinBot

async def setup(bot):
    await bot.add_cog(FlipFlopDerCoinBot(bot))
