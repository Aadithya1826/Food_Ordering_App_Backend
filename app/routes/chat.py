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

    mode = payload.pop("mode", None)
    context = payload.pop("context", None)

    if "systemInstruction" not in payload:
        sys_text = "You are a helpful restaurant voice assistant. You must always return a strictly valid JSON object. Do NOT use unescaped newlines. If the user provides audio, accurately transcribe their words into the 'transcript' field. Then determine the appropriate 'action' and 'speech' response."
        if context:
            sys_text += f"\nContext: {context}"
        
        payload["systemInstruction"] = {
            "parts": [{"text": sys_text}]
        }

    if "generationConfig" not in payload:
        payload["generationConfig"] = {}
        
    payload["generationConfig"]["responseMimeType"] = "application/json"
    payload["generationConfig"]["responseSchema"] = {
        "type": "OBJECT",
        "properties": {
            "transcript": {
                "type": "STRING",
                "description": "The exact words spoken by the user, transcribed from audio. Omit if no audio was provided."
            },
            "speech": {
                "type": "STRING",
                "description": "What the AI says back to the user. Ensure no unescaped newlines."
            },
            "action": {
                "type": "STRING",
                "description": "Action command like ADD_ITEM, REMOVE_ITEM, OPEN_MENU, CLEAR_CART, TRACK_ORDER, SHOW_HELP, UPDATE_DETAILS, PROCEED_TO_PAYMENT."
            },
            "parameters": {
                "type": "OBJECT",
                "description": "Key-value pairs for the action, e.g. {'name': 'curd', 'quantity': 1}.",
                "properties": {
                    "name": {
                        "type": "STRING",
                        "description": "The name of the item, category, or parameter."
                    },
                    "quantity": {
                        "type": "INTEGER",
                        "description": "The numerical quantity of items to add or modify."
                    },
                    "category": {
                        "type": "STRING",
                        "description": "The menu category name."
                    },
                    "method": {
                        "type": "STRING",
                        "description": "Payment method (e.g. Cash, UPI)."
                    },
                    "phone": {
                        "type": "STRING",
                        "description": "Phone number."
                    },
                    "fullName": {
                        "type": "STRING",
                        "description": "Full name of the customer."
                    }
                }
            },
            "intent": {
                "type": "BOOLEAN",
                "description": "True if there is an action, false otherwise."
            }
        },
        "required": ["speech", "intent"]
    }

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
