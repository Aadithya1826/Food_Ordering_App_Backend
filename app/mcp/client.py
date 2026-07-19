import json
import os
import re

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Fallback if .env still has the deprecated model
if GEMINI_MODEL == "gemini-1.5-pro" or GEMINI_MODEL == "gemini-2.5-pro":
    GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_BASE = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta2")

if not GEMINI_API_KEY:
    raise RuntimeError("Environment variable GEMINI_API_KEY is required for Gemini access.")


class GeminiClient:
    def __init__(self) -> None:
        self.base_url = GEMINI_API_BASE
        self.model = GEMINI_MODEL
        self.api_key = GEMINI_API_KEY

    async def generate_text(self, prompt: str, max_tokens: int = 1500, temperature: float = 0.2, audio_base64: str = None, response_mime_type: str = None) -> str:
        # The correct endpoint for gemini-1.5-pro is generateContent, not generateText
        # We'll adapt the base URL to use v1beta and the correct model format
        base_url = self.base_url.replace("v1beta2", "v1beta")
        url = f"{base_url}/models/{self.model}:generateContent?key={self.api_key}"
        
        parts = [{"text": prompt}]
        if audio_base64:
            parts.append({
                "inlineData": {
                    "mimeType": "audio/webm",
                    "data": audio_base64
                }
            })
            
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            }
        }
        
        if response_mime_type:
            payload["generationConfig"]["responseMimeType"] = response_mime_type

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            body = response.json()

        return self._extract_text(body)

    async def generate_json(self, prompt: str, max_tokens: int = 1500, temperature: float = 0.2, audio_base64: str = None) -> dict:
        raw_text = await self.generate_text(prompt, max_tokens=max_tokens, temperature=temperature, audio_base64=audio_base64, response_mime_type="application/json")
        parsed = self._try_parse_json(raw_text)
        if parsed is None:
            raise ValueError(f"Gemini response could not be parsed as JSON. Raw text: {raw_text}")
        return parsed

    async def generate_speech(self, text: str, audio_encoding: str = "MP3") -> dict:
        url = f"{self.base_url}/models/{self.model}:generateSpeech?key={self.api_key}"
        payload = {
            "input": {"text": text},
            "audioConfig": {"audioEncoding": audio_encoding}
        }

        async with httpx.AsyncClient(timeout=40) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()

    def _extract_text(self, response_json: dict) -> str:
        if not response_json:
            return ""

        candidates = response_json.get("candidates") or []
        if not candidates:
            return json.dumps(response_json)

        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        
        text_parts = []
        for chunk in parts:
            if "text" in chunk:
                text_parts.append(chunk["text"])

        if text_parts:
            return "".join(text_parts).strip()

        # fallback
        return str(candidates[0])

    def _try_parse_json(self, raw_text: str) -> dict | None:
        trimmed = raw_text.strip()
        
        # Remove markdown JSON fences if they exist
        if trimmed.startswith("```json"):
            trimmed = trimmed[7:]
        elif trimmed.startswith("```"):
            trimmed = trimmed[3:]
            
        if trimmed.endswith("```"):
            trimmed = trimmed[:-3]
            
        trimmed = trimmed.strip()

        try:
            return json.loads(trimmed)
        except json.JSONDecodeError:
            pass

        json_text = self._extract_first_json_object(trimmed)
        if json_text:
            try:
                return json.loads(json_text)
            except json.JSONDecodeError:
                pass
                
        # Last resort: Salvage partial/truncated JSON using regex
        result = {}
        tool_match = re.search(r'"tool_name"\s*:\s*(null|"[^"]+")', trimmed)
        if tool_match:
            val = tool_match.group(1)
            result["tool_name"] = None if val == "null" else val.strip('"')
            
        params_match = re.search(r'"params"\s*:\s*({[^}]*})', trimmed)
        if params_match:
            try:
                result["params"] = json.loads(params_match.group(1))
            except:
                result["params"] = {}
                
        txt_match = re.search(r'"transcribed_user_text"\s*:\s*"([^"]+)"', trimmed)
        if txt_match:
            result["transcribed_user_text"] = txt_match.group(1)
            
        return result if result else None

    @staticmethod
    def _extract_first_json_object(text: str) -> str | None:
        start_index = None
        depth = 0

        for index, char in enumerate(text):
            if char == "{":
                if start_index is None:
                    start_index = index
                depth += 1
            elif char == "}" and start_index is not None:
                depth -= 1
                if depth == 0:
                    return text[start_index:index + 1]

        return None
