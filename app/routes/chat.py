from fastapi import APIRouter, Request, HTTPException
import httpx
import os

router = APIRouter()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
if GEMINI_MODEL in ["gemini-1.5-pro", "gemini-2.5-pro"]:
    GEMINI_MODEL = "gemini-1.5-flash"

GEMINI_API_BASE = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")

@router.post("/api/chat")
async def proxy_chat(request: Request):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API Key not configured")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    base_url = GEMINI_API_BASE.replace("v1beta2", "v1beta")
    url = f"{base_url}/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
