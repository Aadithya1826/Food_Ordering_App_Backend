from fastapi import APIRouter, Request, HTTPException
import httpx
import os
from app.services.prompt_builder import build_home_agent_prompt, build_full_page_prompt, build_voice_agent_prompt

router = APIRouter()

# Values fetched dynamically to ensure they are loaded after dotenv
def get_gemini_config():
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    if model in ["gemini-1.5-pro", "gemini-2.5-pro"]:
        model = "gemini-1.5-flash"
    base_url = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
    return api_key, model, base_url

@router.post("/api/chat")
async def proxy_chat(request: Request):
    api_key, model, api_base = get_gemini_config()
    
    if not api_key:
        raise HTTPException(status_code=500, detail="Gemini API Key not configured")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    mode = payload.pop("mode", None)
    context = payload.pop("context", {})

    if mode == "home_assistant":
        system_instruction = build_home_agent_prompt(context.get("language", "English"))
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    elif mode == "full_page":
        system_instruction = build_full_page_prompt(context.get("language", "English"))
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    elif mode == "voice_assistant":
        system_instruction = build_voice_agent_prompt(context)
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    # If mode is not provided, we pass the payload directly to Gemini (fallback)

    base_url = api_base.replace("v1beta2", "v1beta")
    url = f"{base_url}/models/{model}:generateContent?key={api_key}"
    
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
