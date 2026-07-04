import asyncio
from app.db import SessionLocal
from app.mcp.tools_extended import get_menu_items
from app.models.user import User

async def run():
    db = SessionLocal()
    user = db.query(User).first()
    res = get_menu_items(db, user, {})
    print(res)

asyncio.run(run())
