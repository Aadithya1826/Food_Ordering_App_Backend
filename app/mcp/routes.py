import asyncio
import re
import base64
import io
import os
import time
from collections import OrderedDict
# pyrefly: ignore [missing-import]
import edge_tts
# pyrefly: ignore [missing-import]
from gtts import gTTS
# pyrefly: ignore [missing-import]
import json
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException
# pyrefly: ignore [missing-import]
from fastapi.responses import StreamingResponse
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session
# pyrefly: ignore [missing-import]
from ..utils.dependencies import get_current_user, get_db
# pyrefly: ignore [missing-import]
from .client import GeminiClient
# pyrefly: ignore [missing-import]
from .schemas import (
    MCPResponse,
    MCPTextRequest,
    MCPToolRequest,
    MCPVoiceRequest,
    MCPTtsRequest,
    MCPVoiceResponse,
    CustomerMCPRequest,
    FOLLOWUP_RESPONSE_SCHEMA,
)
from .tools import build_tool_prompt, execute_tool, list_tool_definitions

router = APIRouter()
client = GeminiClient()

# Sentence terminators, including the Devanagari/Bengali danda. Splitting only
# on [.?!] meant Hindi/Tamil replies never hit a boundary and were therefore
# synthesised as one late block instead of streaming incrementally.
_SENTENCE_BOUNDARY = re.compile(r'([.?!।॥]+(?:\s|\n|$)+)')
# Don't fire TTS for fragments shorter than this; a 2-word clip sounds clipped
# and costs a wasted synthesis round-trip.
_MIN_TTS_CHARS = 12

# --------------------------------------------------------------------------- #
# Edge-TTS circuit breaker
#
# In some deployments (AWS/ECS egress, datacentre IP ranges) Microsoft's
# consumer speech endpoint answers 403 on *every* connection. The old code
# still tried Edge-TTS - twice, primary voice then English - on every single
# turn before finally falling back to gTTS. Each failed attempt pays a full
# websocket handshake + timeout, so the customer heard nothing for several
# seconds after the text had already rendered.
#
# Once Edge-TTS has failed repeatedly we trip the breaker and route straight to
# gTTS for a cooldown window, then probe again. This removes the dead round
# trips that dominate perceived voice latency.
# --------------------------------------------------------------------------- #
_EDGE_TTS_TIMEOUT = float(os.getenv("EDGE_TTS_TIMEOUT", "6"))
_EDGE_TTS_FAILURE_THRESHOLD = int(os.getenv("EDGE_TTS_FAILURE_THRESHOLD", "2"))
_EDGE_TTS_COOLDOWN = float(os.getenv("EDGE_TTS_COOLDOWN", "300"))
# Set EDGE_TTS_ENABLED=false to skip Edge-TTS entirely (recommended when you
# already know the host cannot reach the Microsoft endpoint).
_EDGE_TTS_ENABLED = os.getenv("EDGE_TTS_ENABLED", "true").strip().lower() not in (
    "false",
    "0",
    "no",
)

_edge_tts_failures = 0
_edge_tts_disabled_until = 0.0

_DEFAULT_VOICE = "en-IN-PrabhatNeural"

# Short canned confirmations ("Okay, got it.", "Here is your cart.") are spoken
# on a large share of turns. Synthesising them from scratch every time adds a
# needless network round-trip to the critical path, so memoise them.
_TTS_CACHE: "OrderedDict[tuple[str, str], str]" = OrderedDict()
_TTS_CACHE_MAX = int(os.getenv("TTS_CACHE_SIZE", "256"))
# Only cache short phrases; long bespoke answers would just evict the useful
# entries without ever being reused.
_TTS_CACHE_MAX_CHARS = 160


def _tts_cache_get(key: "tuple[str, str]") -> str | None:
    audio = _TTS_CACHE.get(key)
    if audio is not None:
        _TTS_CACHE.move_to_end(key)
    return audio


def _tts_cache_put(key: "tuple[str, str]", audio: str) -> None:
    _TTS_CACHE[key] = audio
    _TTS_CACHE.move_to_end(key)
    while len(_TTS_CACHE) > _TTS_CACHE_MAX:
        _TTS_CACHE.popitem(last=False)


def _edge_tts_available() -> bool:
    """True when Edge-TTS is worth attempting right now."""
    if not _EDGE_TTS_ENABLED:
        return False
    return time.monotonic() >= _edge_tts_disabled_until


def _record_edge_tts_failure() -> None:
    global _edge_tts_failures, _edge_tts_disabled_until
    _edge_tts_failures += 1
    if _edge_tts_failures >= _EDGE_TTS_FAILURE_THRESHOLD:
        _edge_tts_disabled_until = time.monotonic() + _EDGE_TTS_COOLDOWN
        _edge_tts_failures = 0
        print(
            f"[Edge-TTS] Repeated failures; skipping Edge-TTS for "
            f"{int(_EDGE_TTS_COOLDOWN)}s and using gTTS directly."
        )


def _record_edge_tts_success() -> None:
    global _edge_tts_failures
    _edge_tts_failures = 0


async def _edge_tts_synthesize(text: str, voice: str) -> str | None:
    """One Edge-TTS attempt, hard-bounded by a timeout."""

    async def _stream() -> bytes:
        communicate = edge_tts.Communicate(text, voice, rate="+10%", pitch="+0Hz")
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return audio_data

    audio_data = await asyncio.wait_for(_stream(), timeout=_EDGE_TTS_TIMEOUT)
    if not audio_data:
        return None
    return base64.b64encode(audio_data).decode("utf-8")


def _gtts_lang_for(text: str, lang_hint: str | None, voice: str) -> str:
    """Map detected script / voice onto a gTTS language code."""
    prefix_map = {
        "ta": "ta", "ml": "ml", "kn": "kn", "te": "te",
        "hi": "hi", "ur": "ur", "bn": "bn", "gu": "gu", "pa": "pa",
    }
    voice_prefix = (voice or "").split("-")[0]
    if voice_prefix in prefix_map:
        return prefix_map[voice_prefix]

    hint = (lang_hint or "").lower()
    for name, code in (
        ("tamil", "ta"), ("malayalam", "ml"), ("kannada", "kn"),
        ("telugu", "te"), ("hindi", "hi"), ("urdu", "ur"),
        ("bengali", "bn"), ("gujarati", "gu"), ("punjabi", "pa"),
    ):
        if name in hint:
            return code
    return "en"


async def _gtts_synthesize(text: str, lang: str) -> str | None:
    def run_gtts(text_to_speak: str, language: str) -> str:
        tts = gTTS(text=text_to_speak, lang=language)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return base64.b64encode(fp.read()).decode("utf-8")

    try:
        # Blocking network+IO call, so keep it off the event loop.
        return await asyncio.to_thread(run_gtts, text, lang)
    except Exception as exc:
        print(f"[TTS] gTTS failed (lang={lang}): {exc}")
        if lang != "en":
            try:
                return await asyncio.to_thread(run_gtts, text, "en")
            except Exception as exc_en:
                print(f"[TTS] gTTS English fallback failed: {exc_en}")
        return None


def _select_voice(text: str, lang_hint: str | None) -> str:
    has_tamil     = bool(re.search(r'[\u0B80-\u0BFF]', text))
    has_devanag   = bool(re.search(r'[\u0900-\u097F]', text))
    has_malayalam = bool(re.search(r'[\u0D00-\u0D7F]', text))
    has_kannada   = bool(re.search(r'[\u0C80-\u0CFF]', text))
    has_telugu    = bool(re.search(r'[\u0C00-\u0C7F]', text))
    has_urdu      = bool(re.search(r'[\u0600-\u06FF]', text))
    has_bengali   = bool(re.search(r'[\u0980-\u09FF]', text))
    has_gujarati  = bool(re.search(r'[\u0A80-\u0AFF]', text))
    has_gurmukhi  = bool(re.search(r'[\u0A00-\u0A7F]', text))

    if has_tamil:       return 'ta-IN-ValluvarNeural'
    if has_malayalam:   return 'ml-IN-MidhunNeural'
    if has_kannada:     return 'kn-IN-GaganNeural'
    if has_telugu:      return 'te-IN-MohanNeural'
    if has_urdu:        return 'ur-IN-SalmanNeural'
    if has_bengali:     return 'bn-IN-BashkarNeural'
    if has_gujarati:    return 'gu-IN-NiranjanNeural'
    if has_gurmukhi:    return 'pa-IN-OjasNeural'
    if has_devanag:     return 'hi-IN-MadhurNeural'

    hint = (lang_hint or '').lower()
    if 'tamil' in hint or 'tanglish' in hint: return 'ta-IN-ValluvarNeural'
    if 'malayalam' in hint: return 'ml-IN-MidhunNeural'
    if 'kannada' in hint: return 'kn-IN-GaganNeural'
    if 'telugu' in hint: return 'te-IN-MohanNeural'
    if 'hindi' in hint or 'hinglish' in hint or 'marathi' in hint: return 'hi-IN-MadhurNeural'
    if 'urdu' in hint: return 'ur-IN-SalmanNeural'
    if 'bengali' in hint: return 'bn-IN-BashkarNeural'
    if 'gujarati' in hint: return 'gu-IN-NiranjanNeural'
    if 'punjabi' in hint: return 'pa-IN-OjasNeural'
    return _DEFAULT_VOICE


async def _generate_tts_audio(text: str, lang_hint: str = None) -> str | None:
    if not text:
        return None

    voice = _select_voice(text, lang_hint)

    cache_key = (text, voice)
    cacheable = len(text) <= _TTS_CACHE_MAX_CHARS
    if cacheable:
        cached = _tts_cache_get(cache_key)
        if cached is not None:
            return cached

    audio = None

    if _edge_tts_available():
        try:
            audio = await _edge_tts_synthesize(text, voice)
            if audio:
                _record_edge_tts_success()
            else:
                _record_edge_tts_failure()
        except asyncio.TimeoutError:
            print(f"[Edge-TTS] Timed out after {_EDGE_TTS_TIMEOUT}s (voice={voice})")
            _record_edge_tts_failure()
        except Exception as e:
            print(f"[Edge-TTS] Error generating audio (voice={voice}): {e}")
            _record_edge_tts_failure()

    if not audio:
        # gTTS is the reliable path. We go straight here while the breaker is
        # open, which is what removes the multi-second stall before audio starts.
        audio = await _gtts_synthesize(text, _gtts_lang_for(text, lang_hint, voice))

    if audio and cacheable:
        _tts_cache_put(cache_key, audio)

    return audio
@router.get("/api/v1/mcp/tools", response_model=list[dict])
def list_tools():
    return list_tool_definitions()


@router.post("/api/v1/mcp/tools/execute", response_model=MCPResponse)
def execute_tool_route(
    request: MCPToolRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = execute_tool(db, user, request.tool_name, request.parameters)
    return MCPResponse(
        assistant_text=f"Executed tool {request.tool_name}",
        tool_name=request.tool_name,
        tool_result=result,
    )


@router.post("/api/v1/mcp/natural-language", response_model=MCPResponse)
async def natural_language_query(
    request: MCPTextRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    prompt = build_tool_prompt(user, is_voice=request.is_voice)
    
    history_text = ""
    if request.chat_history:
        history_text = "\n--- Conversation History ---\n"
        for msg in request.chat_history:
            role = "Assistant" if msg.role == "assistant" else "User"
            history_text += f"{role}: {msg.text}\n"
        history_text += "----------------------------\n"

    full_prompt = (
        f"{prompt}\n\n"
        f"{history_text}"
        f"User: {request.prompt}\n"
        "Respond with valid JSON only."
    )

    try:
        parsed = await client.generate_json(
            full_prompt,
            audio_base64=request.audio_base64,
            audio_mime_type=request.audio_mime_type,
        )
    except Exception as e:
        print(f"Error parsing JSON from Gemini: {e}")
        try:
            raw_text = await client.generate_text(
                full_prompt,
                audio_base64=request.audio_base64,
                audio_mime_type=request.audio_mime_type,
            )
            parsed = client._try_parse_json(raw_text)
            if not parsed:
                return MCPResponse(assistant_text=raw_text, tool_name=None, tool_result=None)
        except Exception as e2:
            print(f"Error falling back to text generation: {e2}")
            error_msg = str(e2)
            if "429" in error_msg:
                user_msg = "I'm sorry, my AI service is currently rate-limited (Too Many Requests). Please wait a moment and try again."
            elif "503" in error_msg:
                user_msg = "I'm sorry, my AI service is temporarily unavailable. Please try again later."
            else:
                user_msg = f"I'm sorry, I encountered an AI connection error: {error_msg}"
                
            return MCPResponse(
                assistant_text=user_msg,
                tool_name=None,
                tool_result=None
            )

    tool_name = parsed.get("tool_name")
    assistant_text = parsed.get("assistant_text", "")
    transcribed_user_text = parsed.get("transcribed_user_text", None)
    params = parsed.get("params", {})

    if tool_name:
        try:
            result = execute_tool(db, user, tool_name, params)
            
            ACTION_TOOLS = [
                "navigate_to_page", "trigger_logout", "create_order",
                "update_order_status", "update_menu_item", "update_table_status",
                "update_inventory_stock", "add_to_cart_cashier", "print_cashier_bill",
                "print_future_bill"
            ]
            
            final_assistant_text = assistant_text or f"Invoked tool {tool_name}"
            
            if tool_name not in ACTION_TOOLS:
                # --- Second Pass: Ask Gemini to summarize the tool result (for READ operations) ---
                actual_user_text = transcribed_user_text if transcribed_user_text else request.prompt
                
                clean_instructions = build_tool_prompt(user, is_voice=request.is_voice, is_followup=True)
                
                followup_prompt = (
                    f"{clean_instructions}\n\n"
                    f"The user said: {actual_user_text}\n"
                    f"You used the tool '{tool_name}' which returned this result:\n{result}\n\n"
                    "Provide a natural, conversational response to the user summarizing this data. "
                    "CRITICAL: Follow the exact same slang, dialect, and font rules as instructed above. "
                    "Respond with valid JSON containing ONLY the key: 'assistant_text'."
                )
                try:
                    second_parsed = await client.generate_json(
                        followup_prompt,
                        response_schema=FOLLOWUP_RESPONSE_SCHEMA,
                    )
                    if "assistant_text" in second_parsed:
                        final_assistant_text = second_parsed["assistant_text"]
                except Exception as e:
                    print(f"Error generating follow-up response: {e}")
                # -----------------------------------------------------------

            return MCPResponse(
                assistant_text=final_assistant_text,
                transcribed_user_text=transcribed_user_text,
                tool_name=tool_name,
                tool_result=result,
            )
        except HTTPException as tool_err:
            return MCPResponse(
                assistant_text=f"I couldn't complete that action: {tool_err.detail}",
                transcribed_user_text=transcribed_user_text,
                tool_name=None,
                tool_result=None,
            )
        except Exception as tool_err:
            return MCPResponse(
                assistant_text=f"An unexpected error occurred while running the tool: {str(tool_err)}",
                transcribed_user_text=transcribed_user_text,
                tool_name=None,
                tool_result=None,
            )

    return MCPResponse(
        assistant_text=assistant_text or "I did not detect a tool action.", 
        transcribed_user_text=transcribed_user_text,
        tool_name=None, 
        tool_result=None
    )


@router.post("/api/v1/mcp/voice/ask")
async def voice_assistant_query(
    request: MCPVoiceRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    async def event_generator():
        # Build initial prompt
        prompt = build_tool_prompt(user, is_voice=request.is_voice)
        history_text = ""
        if request.chat_history:
            history_text = "\n--- Conversation History ---\n"
            for msg in request.chat_history:
                role = "Assistant" if msg.role == "assistant" else "User"
                history_text += f"{role}: {msg.text}\n"
            history_text += "----------------------------\n"

        full_prompt = (
            f"{prompt}\n\n"
            f"{history_text}"
            f"User: {request.transcribed_text or 'Audio Payload'}\n"
            "Respond with valid JSON only."
        )

        async def process_stream_and_tts(stream_gen, result_container, lang_hint=None):
            raw_text = ""
            current_live_text = ""
            last_processed_idx = 0

            async for chunk in stream_gen:
                raw_text += chunk
                yield f"data: {json.dumps({'type': 'llm_chunk', 'chunk': chunk})}\n\n"

                current_live_text += chunk
                match = re.search(r'"assistant_text"\s*:\s*"((?:[^"\\]|\\.)*)', current_live_text)
                if match:
                    text_so_far = match.group(1).replace('\\"', '"').replace('\\n', '\n')
                    unprocessed = text_so_far[last_processed_idx:]
                    boundary_match = _SENTENCE_BOUNDARY.search(unprocessed)
                    if boundary_match:
                        boundary_idx = boundary_match.end()
                        sentence = unprocessed[:boundary_idx].strip()
                        # Skip micro-fragments; they sound clipped and waste a
                        # synthesis round-trip. They get flushed with the next
                        # sentence instead.
                        if sentence and len(sentence) >= _MIN_TTS_CHARS:
                            last_processed_idx += boundary_idx
                            audio_chunk = await _generate_tts_audio(sentence, lang_hint)
                            yield f"data: {json.dumps({'type': 'audio', 'payload': audio_chunk, 'fallbackText': sentence})}\n\n"

            # Flush remaining unprocessed text at the end of the stream
            match = re.search(r'"assistant_text"\s*:\s*"((?:[^"\\]|\\.)*)', current_live_text)
            if match:
                text_so_far = match.group(1).replace('\\"', '"').replace('\\n', '\n')
                unprocessed = text_so_far[last_processed_idx:].strip()
                if unprocessed:
                    audio_chunk = await _generate_tts_audio(unprocessed, lang_hint)
                    yield f"data: {json.dumps({'type': 'audio', 'payload': audio_chunk, 'fallbackText': unprocessed})}\n\n"

            result_container.append(raw_text)

        raw_json_container = []
        try:
            stream = client.generate_json_stream(
                full_prompt,
                audio_base64=request.audio_base64,
                audio_mime_type=request.audio_mime_type,
            )
            async for item in process_stream_and_tts(stream, raw_json_container):
                yield item
        except Exception as e:
            print(f"Error streaming JSON from Gemini: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            return
            
        raw_json = raw_json_container[0] if raw_json_container else ""

        parsed = client._try_parse_json(raw_json)
        if not parsed:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Failed to parse JSON'})}\n\n"
            return
            
        tool_name = parsed.get("tool_name")
        assistant_text = parsed.get("assistant_text", "")
        transcribed_user_text = parsed.get("transcribed_user_text", None)
        params = parsed.get("params", {})
        
        if transcribed_user_text:
            yield f"data: {json.dumps({'type': 'transcription', 'text': transcribed_user_text})}\n\n"

        if tool_name:
            try:
                result = execute_tool(db, user, tool_name, params)
                yield f"data: {json.dumps({'type': 'tool_call', 'tool_name': tool_name, 'tool_result': result})}\n\n"
                
                ACTION_TOOLS = [
                    "navigate_to_page", "trigger_logout", "create_order",
                    "update_order_status", "update_menu_item", "update_table_status",
                    "update_inventory_stock", "add_to_cart_cashier", "print_cashier_bill",
                    "print_future_bill"
                ]
                
                if tool_name not in ACTION_TOOLS:
                    # Second pass
                    clean_instructions = build_tool_prompt(user, is_voice=request.is_voice, is_followup=True)
                    actual_user = transcribed_user_text if transcribed_user_text else request.transcribed_text
                    followup_prompt = (
                        f"{clean_instructions}\n\n"
                        f"The user said: {actual_user}\n"
                        f"You used the tool '{tool_name}' which returned this result:\n{result}\n\n"
                        "Provide a natural, conversational response summarizing this data in the appropriate regional language. "
                        "Respond with valid JSON containing ONLY the key: 'assistant_text'."
                    )
                    
                    second_raw_container = []
                    yield f"data: {json.dumps({'type': 'second_pass_start'})}\n\n"
                    try:
                        stream2 = client.generate_json_stream(
                            followup_prompt,
                            response_schema=FOLLOWUP_RESPONSE_SCHEMA,
                        )
                        async for item in process_stream_and_tts(stream2, second_raw_container):
                            yield item

                        second_raw = second_raw_container[0] if second_raw_container else ""
                        second_parsed = client._try_parse_json(second_raw)
                        if second_parsed and "assistant_text" in second_parsed:
                            assistant_text = second_parsed["assistant_text"]
                    except Exception as e:
                        print(f"Error in second pass: {e}")
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")





@router.post("/api/v1/mcp/voice/tts", response_model=MCPVoiceResponse)
async def synthesize_voice(
    request: MCPTtsRequest,
    user=Depends(get_current_user),
):
    try:
        audio_body = await client.generate_speech(request.text, audio_encoding=request.audio_encoding)
        return MCPVoiceResponse(
            assistant_text=request.text,
            tool_name=None,
            tool_result=None,
            audio_payload=audio_body,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"TTS generation failed: {exc}")
