import httpx
import os
import asyncio

async def main():
    api_key = os.getenv("GEMINI_API_KEY", "")
    print(f"Key length: {len(api_key)}")
    if not api_key:
        print("NO KEY")
        return

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": "Hello"}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 600,
        }
    }
    
    async with httpx.AsyncClient() as client:
        res = await client.post(url, json=payload)
        print("Status:", res.status_code)
        print("Body:", res.text)

if __name__ == "__main__":
    asyncio.run(main())
