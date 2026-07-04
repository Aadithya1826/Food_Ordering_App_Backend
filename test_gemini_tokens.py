import asyncio
from app.mcp.client import GeminiClient

async def run():
    client = GeminiClient()
    prompt = "Write a long story in Tamil, at least 500 words."
    res = await client.generate_text(prompt, max_tokens=1500)
    print("LENGTH OF RESPONSE:", len(res))
    print("RESPONSE ENDS WITH:", res[-100:])

asyncio.run(run())
