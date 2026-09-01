import re
import base64
import io
import edge_tts
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..utils.dependencies import get_current_user, get_db
from .client import GeminiClient
from .schemas import (
    MCPResponse,
    MCPTextRequest,
    MCPToolRequest,
    MCPVoiceRequest,
    MCPTtsRequest,
    MCPVoiceResponse,
)
from .tools import build_tool_prompt, execute_tool, list_tool_definitions

router = APIRouter()
client = GeminiClient()


async def _generate_tts_audio(text: str, lang_hint: str = None) -> str | None:
    if not text: return None
    has_tamil     = bool(re.search(r'[\u0B80-\u0BFF]', text))
    has_devanag   = bool(re.search(r'[\u0900-\u097F]', text))
    has_malayalam = bool(re.search(r'[\u0D00-\u0D7F]', text))
    has_kannada   = bool(re.search(r'[\u0C80-\u0CFF]', text))
    has_telugu    = bool(re.search(r'[\u0C00-\u0C7F]', text))
    has_urdu      = bool(re.search(r'[\u0600-\u06FF]', text))
    has_bengali   = bool(re.search(r'[\u0980-\u09FF]', text))
    has_gujarati  = bool(re.search(r'[\u0A80-\u0AFF]', text))
    has_gurmukhi  = bool(re.search(r'[\u0A00-\u0A7F]', text))

    if has_tamil:       voice = 'ta-IN-ValluvarNeural'
    elif has_malayalam: voice = 'ml-IN-MidhunNeural'
    elif has_kannada:   voice = 'kn-IN-GaganNeural'
    elif has_telugu:    voice = 'te-IN-MohanNeural'
    elif has_urdu:      voice = 'ur-IN-SalmanNeural'
    elif has_bengali:   voice = 'bn-IN-BashkarNeural'
    elif has_gujarati:  voice = 'gu-IN-NiranjanNeural'
    elif has_gurmukhi:  voice = 'pa-IN-OjasNeural'
    elif has_devanag:   voice = 'hi-IN-MadhurNeural'
    else:
        hint = (lang_hint or '').lower()
        if 'tamil' in hint or 'tanglish' in hint: voice = 'ta-IN-ValluvarNeural'
        elif 'malayalam' in hint: voice = 'ml-IN-MidhunNeural'
        elif 'kannada' in hint: voice = 'kn-IN-GaganNeural'
        elif 'telugu' in hint: voice = 'te-IN-MohanNeural'
        elif 'hindi' in hint or 'hinglish' in hint or 'marathi' in hint: voice = 'hi-IN-MadhurNeural'
        elif 'urdu' in hint: voice = 'ur-IN-SalmanNeural'
        elif 'bengali' in hint: voice = 'bn-IN-BashkarNeural'
        elif 'gujarati' in hint: voice = 'gu-IN-NiranjanNeural'
        elif 'punjabi' in hint: voice = 'pa-IN-OjasNeural'
        else: voice = 'en-IN-PrabhatNeural'

    try:
        # +10% rate makes the TTS sound much more like a brisk, natural human conversation 
        # instead of a slow, robotic news-reader.
        communicate = edge_tts.Communicate(text, voice, rate="+10%", pitch="+0Hz")
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        return base64.b64encode(audio_data).decode('utf-8')
    except Exception as e:
        print(f"[Edge-TTS] Error generating audio (voice={voice}): {e}")
        if voice != 'en-IN-PrabhatNeural':
            try:
                communicate = edge_tts.Communicate(text, 'en-IN-PrabhatNeural', rate="+10%", pitch="+0Hz")
                audio_data = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_data += chunk["data"]
                return base64.b64encode(audio_data).decode('utf-8')
            except Exception as e2:
                print(f"[Edge-TTS Fallback] Error generating audio (voice=en-IN-PrabhatNeural): {e2}")
        
        # Final Fallback to Google Translate TTS if Edge-TTS completely fails in the deployed environment
        try:
            print("[TTS] Falling back to gTTS...")
            import asyncio
            from gtts import gTTS
            import io
            
            def run_gtts(text_to_speak, lang):
                tts = gTTS(text=text_to_speak, lang=lang)
                fp = io.BytesIO()
                tts.write_to_fp(fp)
                fp.seek(0)
                return base64.b64encode(fp.read()).decode('utf-8')
            
            fallback_lang = "en"
            if lang_hint:
                if "ta" in lang_hint: fallback_lang = "ta"
                elif "hi" in lang_hint: fallback_lang = "hi"
                elif "ml" in lang_hint: fallback_lang = "ml"
                elif "te" in lang_hint: fallback_lang = "te"
            
            # Run the blocking gTTS call in a background thread to prevent pausing the event loop
            return await asyncio.to_thread(run_gtts, text, fallback_lang)
        except Exception as gtts_e:
            print(f"[TTS Fallback] gTTS also failed: {gtts_e}")
            return None
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
        parsed = await client.generate_json(full_prompt, audio_base64=request.audio_base64)
    except Exception as e:
        print(f"Error parsing JSON from Gemini: {e}")
        try:
            raw_text = await client.generate_text(full_prompt, audio_base64=request.audio_base64)
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
                "update_inventory_stock"
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
                    second_parsed = await client.generate_json(followup_prompt)
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

        async def process_stream_and_tts(stream_gen, result_container):
            raw_text = ""
            current_live_text = ""
            last_processed_idx = 0
            import re
            
            async for chunk in stream_gen:
                raw_text += chunk
                yield f"data: {json.dumps({'type': 'llm_chunk', 'chunk': chunk})}\n\n"
                
                current_live_text += chunk
                match = re.search(r'"assistant_text"\s*:\s*"((?:[^"\\]|\\.)*)', current_live_text)
                if match:
                    text_so_far = match.group(1).replace('\\"', '"').replace('\\n', '\n')
                    unprocessed = text_so_far[last_processed_idx:]
                    boundary_match = re.search(r'([.?!]+(?:\s|\n|$)+)', unprocessed)
                    if boundary_match:
                        boundary_idx = boundary_match.end()
                        sentence = unprocessed[:boundary_idx].strip()
                        last_processed_idx += boundary_idx
                        if sentence:
                            audio_chunk = await _generate_tts_audio(sentence)
                            yield f"data: {json.dumps({'type': 'audio', 'payload': audio_chunk, 'fallbackText': sentence})}\n\n"
            
            # Flush remaining unprocessed text at the end of the stream
            match = re.search(r'"assistant_text"\s*:\s*"((?:[^"\\]|\\.)*)', current_live_text)
            if match:
                text_so_far = match.group(1).replace('\\"', '"').replace('\\n', '\n')
                unprocessed = text_so_far[last_processed_idx:].strip()
                if unprocessed:
                    audio_chunk = await _generate_tts_audio(unprocessed)
                    yield f"data: {json.dumps({'type': 'audio', 'payload': audio_chunk, 'fallbackText': unprocessed})}\n\n"
                        
            result_container.append(raw_text)

        raw_json_container = []
        try:
            stream = client.generate_json_stream(full_prompt, audio_base64=request.audio_base64)
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
                    "update_inventory_stock"
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
                        stream2 = client.generate_json_stream(followup_prompt)
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
