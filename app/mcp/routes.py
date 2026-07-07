import re
import base64
import io
from gtts import gTTS
from fastapi import APIRouter, Depends, HTTPException
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
            
            # --- Second Pass: Ask Gemini to summarize the tool result ---
            final_assistant_text = assistant_text or f"Invoked tool {tool_name}"
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


@router.post("/api/v1/mcp/voice/ask", response_model=MCPVoiceResponse)
async def voice_assistant_query(
    request: MCPVoiceRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    text_request = MCPTextRequest(
        prompt=request.transcribed_text, 
        restaurant_id=request.restaurant_id,
        chat_history=request.chat_history,
        audio_base64=request.audio_base64,
        is_voice=request.is_voice
    )
    response = await natural_language_query(text_request, user=user, db=db)
    
    # Generate high-quality TTS audio for regional languages using gTTS
    audio_base64 = None
    assistant_text = response.assistant_text
    if assistant_text:
        has_tamil = bool(re.search(r'[\u0B80-\u0BFF]', assistant_text))
        has_hindi = bool(re.search(r'[\u0900-\u097F]', assistant_text))
        lang = 'ta' if has_tamil else ('hi' if has_hindi else 'en')
        
        try:
            # We use gTTS to generate the audio in-memory
            tts = gTTS(text=assistant_text, lang=lang, slow=False)
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            audio_base64 = base64.b64encode(fp.read()).decode('utf-8')
        except Exception as e:
            print(f"Error generating gTTS audio: {e}")

    return MCPVoiceResponse(
        assistant_text=response.assistant_text,
        transcribed_user_text=response.transcribed_user_text,
        tool_name=response.tool_name,
        tool_result=response.tool_result,
        audio_payload=audio_base64,
    )


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
