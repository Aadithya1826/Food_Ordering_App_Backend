import asyncio
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.mcp.routes import natural_language_query
from app.mcp.schemas import MCPTextRequest
from app.models.user import User

async def test():
    db = SessionLocal()
    user = db.query(User).first()
    req = MCPTextRequest(
        prompt="ஓகே, மொத்தம் எவ்ளோ டேபிள்ஸ் ஆக்டிவா இருக்குன்னு பாத்து சொல்லு",
        restaurant_id=1,
        is_voice=True
    )
    res = await natural_language_query(req, user=user, db=db)
    print("ASSISTANT TEXT:", repr(res.assistant_text))
    db.close()

asyncio.run(test())
