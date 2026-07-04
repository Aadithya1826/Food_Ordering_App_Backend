import asyncio
import httpx

async def test():
    async with httpx.AsyncClient() as client:
        req = {
            "transcribed_text": "ஓகே, மொத்தம் எவ்ளோ டேபிள்ஸ் ஆக்டிவா இருக்குன்னு பாத்து சொல்லு",
            "chat_history": [],
            "restaurant_id": 1
        }
        res = await client.post("http://127.0.0.1:8000/api/v1/mcp/voice/ask", json=req, timeout=30)
        print(res.text)

asyncio.run(test())
