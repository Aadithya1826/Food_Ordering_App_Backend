import asyncio
from app.mcp.client import GeminiClient

async def run():
    client = GeminiClient()
    prompt = """
CRITICAL INSTRUCTION: Adopt a natural, helpful, and professional conversational tone.
- Communicate clearly and politely. You MUST strictly match the language of the user's MOST RECENT message. If their latest message is purely in English, you MUST reply purely in English. If they mix English and a regional language (like Thanglish), reply in the regional language natively.
- ALWAYS use standard plain text characters. NEVER use bold, italics, markdown, emojis, mathematical alphanumeric symbols, or extended unicode blocks. For Tamil, use ONLY standard Unicode U+0B80-U+0BFF.

The user said: Okay, what about tomato raita?
You used the tool 'search_menu_item' which returned this result:
[]

Provide a natural, conversational response to the user summarizing this data. CRITICAL: Follow the exact same slang, dialect, and font rules as instructed above. Respond with valid JSON containing ONLY the key: 'assistant_text'.
"""
    res = await client.generate_json(prompt)
    print(res)

asyncio.run(run())
