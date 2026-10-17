from fastapi import APIRouter, Request, HTTPException
import httpx
import os

router = APIRouter()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Keep this in sync with app/mcp/client.py. gemini-1.5-* is retired and now
# 404s, which made this proxy return a 404 body as the assistant's reply.
DEFAULT_MODEL = "gemini-2.5-flash"


def _resolve_model() -> str:
    model = (
        os.getenv("GEMINI_MODEL_CHAT")
        or os.getenv("GEMINI_CHAT_MODEL")
        or os.getenv("GEMINI_MODEL")
        or DEFAULT_MODEL
    ).strip()
    if model in ("gemini-1.5-pro", "gemini-1.5-flash", "gemini-pro", "gemini-3.7-flash"):
        return DEFAULT_MODEL
    return model


GEMINI_MODEL = _resolve_model()

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
