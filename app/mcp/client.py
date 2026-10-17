import asyncio
import json
import os
import random
import re

import httpx

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

DEFAULT_MODEL = "gemini-2.5-flash"

# Models that no longer exist / were retired on the Generative Language API.
# Anything listed here is silently upgraded to a currently-served model so a
# stale .env can never take the whole voice agent down.
_RETIRED_MODELS = {
    "gemini-1.5-flash": DEFAULT_MODEL,
    "gemini-1.5-pro": DEFAULT_MODEL,
    "gemini-1.0-pro": DEFAULT_MODEL,
    "gemini-pro": DEFAULT_MODEL,
    # This one never existed at all - it was hard-coded as both the default and
    # the "fallback", which meant every single request 404'd.
    "gemini-3.7-flash": DEFAULT_MODEL,
}

def _normalize_model_name(model_name: str | None, fallback: str) -> str:
    configured = (model_name or "").strip() or fallback
    return _RETIRED_MODELS.get(configured, configured)


# Suffixes that identify a *non-text* model. These only serve dedicated
# endpoints (speech synthesis, image generation, embeddings) and return
# 400 Bad Request when posted to :generateContent. Sending the voice agent's
# JSON reasoning prompt to `-tts` was exactly the 400 seen in production.
_NON_TEXT_MODEL_MARKERS = (
    "-tts",
    "-image",
    "-embedding",
    "-transcribe",
    "embedding",
)


def _is_text_capable(model_name: str) -> bool:
    name = (model_name or "").strip().lower()
    if not name:
        return False
    return not any(marker in name for marker in _NON_TEXT_MODEL_MARKERS)


def _resolve_text_model(model_name: str, fallback: str) -> str:
    """Guarantee the model we hand to :generateContent can actually do text."""
    normalized = _normalize_model_name(model_name, fallback)
    if _is_text_capable(normalized):
        return normalized
    safe_fallback = _normalize_model_name(fallback, DEFAULT_MODEL)
    if not _is_text_capable(safe_fallback):
        safe_fallback = DEFAULT_MODEL
    print(
        f"[Gemini] '{normalized}' cannot serve generateContent; "
        f"falling back to '{safe_fallback}'."
    )
    return safe_fallback


_configured_default_model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
_configured_chat_model = os.getenv("GEMINI_MODEL_CHAT") or os.getenv("GEMINI_CHAT_MODEL")
_configured_voice_model = os.getenv("GEMINI_MODEL_VOICE") or os.getenv("GEMINI_VOICE_MODEL")
_configured_tts_model = os.getenv("GEMINI_MODEL_TTS") or os.getenv("GEMINI_TTS_MODEL")
_configured_transcribe_model = os.getenv("GEMINI_MODEL_TRANSCRIBE") or os.getenv("GEMINI_TRANSCRIBE_MODEL")
_configured_fallback_model = os.getenv("GEMINI_MODEL_FALLBACK") or DEFAULT_MODEL

# Last-resort text model. Must itself be text-capable, hence the guard.
GEMINI_FALLBACK_MODEL = _normalize_model_name(_configured_fallback_model, DEFAULT_MODEL)
if not _is_text_capable(GEMINI_FALLBACK_MODEL):
    GEMINI_FALLBACK_MODEL = DEFAULT_MODEL

GEMINI_MODEL = _resolve_text_model(_configured_default_model, GEMINI_FALLBACK_MODEL)

# Chat + voice are both *reasoning* turns: they post text/audio to
# generateContent and expect JSON back, so both must be text-capable models.
GEMINI_CHAT_MODEL = _resolve_text_model(_configured_chat_model or GEMINI_MODEL, GEMINI_MODEL)
GEMINI_VOICE_MODEL = _resolve_text_model(_configured_voice_model or GEMINI_MODEL, GEMINI_MODEL)

# These two are only ever used against their dedicated endpoints, so they are
# allowed to be specialised (-tts / -transcribe) models.
GEMINI_TTS_MODEL = _normalize_model_name(_configured_tts_model or _configured_voice_model, GEMINI_MODEL)
GEMINI_TRANSCRIBE_MODEL = _normalize_model_name(_configured_transcribe_model, GEMINI_VOICE_MODEL)

_MODELS_BY_MODE = {
    "chat": GEMINI_CHAT_MODEL,
    "voice": GEMINI_VOICE_MODEL,
    "tts": GEMINI_TTS_MODEL,
    "transcribe": GEMINI_TRANSCRIBE_MODEL,
}

# v1beta2 was decommissioned; normalise any legacy value to v1beta.
GEMINI_API_BASE = os.getenv(
    "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
).replace("v1beta2", "v1beta")

# Latency budget. A voice turn feels broken past ~4s, so fail fast and retry
# rather than hanging on a single 30s socket.
_CONNECT_TIMEOUT = float(os.getenv("GEMINI_CONNECT_TIMEOUT", "5"))
_READ_TIMEOUT = float(os.getenv("GEMINI_READ_TIMEOUT", "25"))
_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "3"))

# Audio container the browser actually recorded. Chrome/Firefox MediaRecorder
# emits webm/opus, Safari emits mp4/aac. Sending the wrong mime makes Gemini
# transcribe garbage - a major source of "the agent misheard me".
SUPPORTED_AUDIO_MIME = {
    "audio/webm",
    "audio/ogg",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/aac",
    "audio/flac",
}

if not GEMINI_API_KEY:
    raise RuntimeError("Environment variable GEMINI_API_KEY is required for Gemini access.")


class GeminiClient:
    """Thin async wrapper over the Gemini generateContent API.

    Shares one connection pool across the whole process so we are not paying a
    fresh TLS handshake (~150-300ms) on every conversational turn.
    """

    _shared_client: httpx.AsyncClient | None = None

    def __init__(self, mode: str = "chat", model: str | None = None) -> None:
        self.base_url = GEMINI_API_BASE
        resolved_mode = (mode or "chat").strip().lower()
        selected_model = model or _MODELS_BY_MODE.get(resolved_mode) or GEMINI_MODEL

        # 'tts' / 'transcribe' talk to dedicated endpoints and may legitimately
        # be specialised models. Everything else goes to :generateContent and
        # must be text-capable, otherwise the API answers 400.
        if resolved_mode in ("tts", "transcribe"):
            self.model = _normalize_model_name(selected_model, GEMINI_MODEL)
        else:
            self.model = _resolve_text_model(selected_model, GEMINI_FALLBACK_MODEL)

        self.api_key = GEMINI_API_KEY

    @classmethod
    def _http(cls) -> httpx.AsyncClient:
        if cls._shared_client is None or cls._shared_client.is_closed:
            cls._shared_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    _READ_TIMEOUT, connect=_CONNECT_TIMEOUT, write=10.0, pool=5.0
                ),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
                headers={"x-goog-api-key": GEMINI_API_KEY},
            )
        return cls._shared_client

    @classmethod
    async def aclose(cls) -> None:
        if cls._shared_client is not None and not cls._shared_client.is_closed:
            await cls._shared_client.aclose()
        cls._shared_client = None

    # ------------------------------------------------------------------ #
    # payload construction
    # ------------------------------------------------------------------ #
    def _build_payload(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        audio_base64: str | None,
        audio_mime_type: str | None,
        response_mime_type: str | None,
        response_schema: dict | None,
        system_instruction: str | None,
        low_latency: bool,
    ) -> dict:
        parts: list[dict] = []
        if audio_base64:
            mime = (audio_mime_type or "audio/webm").split(";")[0].strip().lower()
            if mime not in SUPPORTED_AUDIO_MIME:
                mime = "audio/webm"
            parts.append({"inlineData": {"mimeType": mime, "data": audio_base64}})
        parts.append({"text": prompt})

        generation_config: dict = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "candidateCount": 1,
        }

        if response_mime_type:
            generation_config["responseMimeType"] = response_mime_type
        if response_schema:
            generation_config["responseSchema"] = response_schema

        # 2.5 Flash defaults to dynamic "thinking", which can silently add
        # several seconds per turn. For a latency-critical voice loop we turn it
        # off; the task is slot extraction + a short reply, not deep reasoning.
        if low_latency and self.model.startswith(("gemini-2.5", "gemini-3")):
            generation_config["thinkingConfig"] = {"thinkingBudget": 0}

        payload: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation_config,
            # Restaurant chatter and regional slang can trip the default filters
            # and return an empty candidate, which surfaces to the customer as
            # the agent simply ignoring them.
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_ONLY_HIGH"}
                for c in (
                    "HARM_CATEGORY_HARASSMENT",
                    "HARM_CATEGORY_HATE_SPEECH",
                    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "HARM_CATEGORY_DANGEROUS_CONTENT",
                )
            ],
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        return payload

    async def _post_with_retry(self, url: str, payload: dict) -> dict:
        """POST with exponential backoff on transient failures.

        Previously a single 429/503 (very common on the free tier) produced a
        canned "sorry, I didn't understand" reply, which reads to the customer
        as the agent being dumb rather than rate-limited.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = await self._http().post(url, json=payload)
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt < _MAX_RETRIES - 1:
                        retry_after = response.headers.get("retry-after", "")
                        delay = (
                            float(retry_after)
                            if retry_after.isdigit()
                            else (0.4 * (2**attempt)) + random.uniform(0, 0.25)
                        )
                        await asyncio.sleep(min(delay, 4.0))
                        continue
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep((0.4 * (2**attempt)) + random.uniform(0, 0.25))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("Gemini request failed after retries")

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 1500,
        temperature: float = 0.2,
        audio_base64: str | None = None,
        response_mime_type: str | None = None,
        response_schema: dict | None = None,
        audio_mime_type: str | None = None,
        system_instruction: str | None = None,
        low_latency: bool = True,
    ) -> str:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        payload = self._build_payload(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            audio_base64=audio_base64,
            audio_mime_type=audio_mime_type,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
            system_instruction=system_instruction,
            low_latency=low_latency,
        )
        body = await self._post_with_retry(url, payload)
        return self._extract_text(body)

    async def generate_json(
        self,
        prompt: str,
        max_tokens: int = 1500,
        temperature: float = 0.2,
        audio_base64: str | None = None,
        response_schema: dict | None = None,
        audio_mime_type: str | None = None,
        system_instruction: str | None = None,
        low_latency: bool = True,
    ) -> dict:
        raw_text = await self.generate_text(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            audio_base64=audio_base64,
            response_mime_type="application/json",
            response_schema=response_schema,
            audio_mime_type=audio_mime_type,
            system_instruction=system_instruction,
            low_latency=low_latency,
        )
        parsed = self._try_parse_json(raw_text)
        if parsed is None:
            raise ValueError(
                f"Gemini response could not be parsed as JSON. Raw text: {raw_text[:500]}"
            )
        return parsed

    async def generate_json_stream(
        self,
        prompt: str,
        max_tokens: int = 1500,
        temperature: float = 0.2,
        audio_base64: str | None = None,
        response_schema: dict | None = None,
        audio_mime_type: str | None = None,
        system_instruction: str | None = None,
        low_latency: bool = True,
    ):
        """Real token-by-token streaming via streamGenerateContent + SSE.

        The previous implementation awaited the *entire* response and then
        yielded it as a single chunk, so the incremental-TTS logic downstream
        could never start speaking until the model had completely finished.
        That is a primary reason the voice agent felt laggy and "fumbly".
        """
        url = f"{self.base_url}/models/{self.model}:streamGenerateContent?alt=sse"
        payload = self._build_payload(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            audio_base64=audio_base64,
            audio_mime_type=audio_mime_type,
            response_mime_type="application/json",
            response_schema=response_schema,
            system_instruction=system_instruction,
            low_latency=low_latency,
        )

        try:
            async with self._http().stream("POST", url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    text = self._extract_text(chunk)
                    if text:
                        yield text
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as exc:
            # Streaming is best-effort; fall back to a single buffered call so a
            # transient stream failure still produces an answer.
            print(f"[Gemini] stream failed ({exc}); falling back to buffered call")
            raw = await self.generate_text(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                audio_base64=audio_base64,
                response_mime_type="application/json",
                response_schema=response_schema,
                audio_mime_type=audio_mime_type,
                system_instruction=system_instruction,
                low_latency=low_latency,
            )
            if raw:
                yield raw

    async def generate_speech(self, text: str, audio_encoding: str = "MP3") -> dict:
        url = f"{self.base_url}/models/{self.model}:generateSpeech"
        payload = {
            "input": {"text": text},
            "audioConfig": {"audioEncoding": audio_encoding},
        }
        response = await self._http().post(url, json=payload, timeout=40)
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------ #
    # parsing helpers
    # ------------------------------------------------------------------ #
    def _extract_text(self, response_json: dict) -> str:
        if not response_json:
            return ""

        candidates = response_json.get("candidates") or []
        if not candidates:
            # Surface prompt-level blocks instead of silently returning the raw
            # envelope, which used to get read aloud to the customer.
            feedback = response_json.get("promptFeedback") or {}
            if feedback.get("blockReason"):
                print(f"[Gemini] prompt blocked: {feedback.get('blockReason')}")
            return ""

        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []

        text_parts = [chunk["text"] for chunk in parts if "text" in chunk]
        if text_parts:
            return "".join(text_parts)

        finish = candidates[0].get("finishReason")
        if finish and finish not in ("STOP", "MAX_TOKENS"):
            print(f"[Gemini] candidate returned no text (finishReason={finish})")
        return ""

    def _try_parse_json(self, raw_text: str) -> dict | None:
        if not raw_text:
            return None

        trimmed = raw_text.strip()

        # Strip markdown fences if the model added them anyway.
        if trimmed.startswith("```"):
            trimmed = re.sub(r"^```(?:json)?\s*", "", trimmed)
            trimmed = re.sub(r"\s*```$", "", trimmed)
            trimmed = trimmed.strip()

        try:
            parsed = json.loads(trimmed)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

        json_text = self._extract_first_json_object(trimmed)
        if json_text:
            try:
                parsed = json.loads(json_text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        # Last resort: salvage individual fields from truncated JSON.
        result: dict = {}
        tool_match = re.search(r'"tool_name"\s*:\s*(null|"[^"]+")', trimmed)
        if tool_match:
            val = tool_match.group(1)
            result["tool_name"] = None if val == "null" else val.strip('"')

        params_match = re.search(r'"params"\s*:\s*({[^}]*})', trimmed)
        if params_match:
            try:
                result["params"] = json.loads(params_match.group(1))
            except json.JSONDecodeError:
                result["params"] = {}

        txt_match = re.search(r'"transcribed_user_text"\s*:\s*"([^"]*)"', trimmed)
        if txt_match:
            result["transcribed_user_text"] = txt_match.group(1)

        ui_actions_match = re.search(r'"ui_actions"\s*:\s*(\[.*?\])', trimmed, re.DOTALL)
        if ui_actions_match:
            try:
                result["ui_actions"] = json.loads(ui_actions_match.group(1))
            except json.JSONDecodeError:
                result["ui_actions"] = []

        ast_match = re.search(r'"assistant_text"\s*:\s*"((?:[^"\\]|\\.)*)"', trimmed)
        if ast_match:
            raw_val = ast_match.group(1)
            try:
                # json.loads handles \uXXXX correctly; the old unicode_escape
                # call mangled non-ASCII (Tamil/Hindi) replies into mojibake.
                result["assistant_text"] = json.loads(f'"{raw_val}"')
            except json.JSONDecodeError:
                result["assistant_text"] = raw_val

        return result if result else None

    @staticmethod
    def _extract_first_json_object(text: str) -> str | None:
        start_index = None
        depth = 0
        in_string = False
        escaped = False

        for index, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                if start_index is None:
                    start_index = index
                depth += 1
            elif char == "}" and start_index is not None:
                depth -= 1
                if depth == 0:
                    return text[start_index : index + 1]

        return None

