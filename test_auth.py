import asyncio
from app.db import SessionLocal
from app.mcp.routes import natural_language_query
from app.mcp.schemas import MCPTextRequest
from app.models.user import User

async def run():
    db = SessionLocal()
    user = db.query(User).filter_by(role="HOTEL_MANAGER").first()
    
    req = MCPTextRequest(
        prompt="ஓகே, மொத்தம் எவ்ளோ டேபிள்ஸ் ஆக்டிவா இருக்குன்னு பாத்து சொல்லு",
        restaurant_id=user.restaurant_id,
        is_voice=True
    )
    
    res = await natural_language_query(req, user=user, db=db)
    print("----- ASSISTANT TEXT -----")
    print(res.assistant_text)
    print("--------------------------")
    db.close()

asyncio.run(run())
