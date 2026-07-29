from fastapi import APIRouter, Request, HTTPException
import httpx
import os
from app.services.prompt_builder import build_home_agent_prompt, build_full_page_prompt, build_voice_agent_prompt

router = APIRouter()

# Values fetched dynamically to ensure they are loaded after dotenv
def get_gemini_config():
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
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

    multilingual_rule = (
        "\n\nLANGUAGE & MULTILINGUAL RESPONSE RULE:\n"
        "Detect the language, dialect, and script of the user's input. You MUST respond in the exact same language, dialect, and script in the 'speech' field.\n"
        "For example:\n"
        "- If the user speaks in Tanglish (Tamil transliterated in English script, e.g. 'oru masala dosa add pannunga'), you MUST reply in Tanglish (e.g. 'Sure, oru masala dosa add pannitten').\n"
        "- If the user speaks in Hinglish (Hindi transliterated in English script, e.g. 'ek masala dosa add karo'), you MUST reply in Hinglish (e.g. 'Sure, ek masala dosa add kar diya').\n"
        "- If the user speaks in Tamil (Tamil script, e.g. 'ஒரு மசாலா தோசை சேர்க்கவும்'), you MUST reply in Tamil script (e.g. 'நிச்சயமாக, ஒரு மசாலா தோசை உங்கள் கார்ட்டில் சேர்க்கப்பட்டது').\n"
        "- If the user speaks in Hindi (Hindi script, e.g. 'एक मसाला डोसा जोड़ें'), you MUST reply in Hindi script (e.g. 'बिलकुल, एक मसाला डोसा आपके कार्ट में जोड़ दिया गया है').\n"
        "- If the user speaks in English, reply in English.\n"
        "- If the user speaks in any other multilingual language (like Spanish, Telugu, Kannada, Malayalam, etc.), you MUST respond in that specific language and script.\n"
        "And transcribe their EXACT words into the 'transcript' field if they provide audio, without changing the item names."
    )

    if mode == "home_assistant":
        system_instruction = build_home_agent_prompt(context.get("language", "English")) + multilingual_rule
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    elif mode == "full_page":
        system_instruction = build_full_page_prompt(context.get("language", "English")) + multilingual_rule
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    elif mode == "voice_assistant":
        system_instruction = build_voice_agent_prompt(context) + multilingual_rule
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    else:
        if "systemInstruction" not in payload:
            sys_text = (
                "You are a helpful restaurant voice assistant. You must always return a strictly valid JSON object. "
                "Do NOT use unescaped newlines. "
                "If the user provides audio, accurately transcribe their EXACT words into the 'transcript' field — "
                "do NOT correct, normalize, or improve their pronunciation. "
                "CRITICAL: When setting parameters.name for ADD_ITEM actions, you MUST use the EXACT item name "
                "as spoken by the user (e.g., if they say 'kambu dosa', use 'kambu dosa' — do NOT change it to "
                "'masala dosa' or any other menu item). The frontend will handle menu item matching. "
            ) + multilingual_rule
            if context:
                sys_text += f"\nContext: {context}"
            payload["systemInstruction"] = {
                "parts": [{"text": sys_text}]
            }

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
