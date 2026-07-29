import sys
import asyncio
from app.mcp.client import GeminiClient

async def main():
    client = GeminiClient()
    prompt = "Reply strictly in Tamil with some local slang. Just say: 'I have checked the tables.'"
    try:
        res = await client.generate_json(prompt, max_tokens=1500)
        print("SUCCESS JSON", res)
    except Exception as e:
        print("FAILED JSON", e)

if __name__ == "__main__":
    asyncio.run(main())
