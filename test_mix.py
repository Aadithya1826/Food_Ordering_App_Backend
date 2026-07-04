import asyncio
from app.db import SessionLocal
from app.mcp.routes import natural_language_query
from app.mcp.schemas import MCPTextRequest
from app.models.user import User

async def run():
    db = SessionLocal()
    user = db.query(User).first()
    
    req = MCPTextRequest(
        prompt="The payments pageக்கு போ",
        restaurant_id=1, 
        is_voice=True
    )
    
    res = await natural_language_query(req, user=user, db=db)
    print("ASSISTANT TEXT:")
    print(repr(res.assistant_text))
    db.close()

asyncio.run(run())
