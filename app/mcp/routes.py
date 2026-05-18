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
    prompt = build_tool_prompt()
    full_prompt = (
        f"{prompt}\n\n"
        f"User: {request.prompt}\n"
        "Respond with valid JSON only."
    )

    try:
        parsed = await client.generate_json(full_prompt)
    except Exception as e:
        print(f"Error parsing JSON from Gemini: {e}")
        try:
            raw_text = await client.generate_text(full_prompt)
            return MCPResponse(assistant_text=raw_text, tool_name=None, tool_result=None)
        except Exception as e2:
            print(f"Error falling back to text generation: {e2}")
            return MCPResponse(
                assistant_text="I'm sorry, my backend connection to the AI model failed.",
                tool_name=None,
                tool_result=None
            )

    tool_name = parsed.get("tool_name")
    assistant_text = parsed.get("assistant_text", "")
    params = parsed.get("params", {})

    if tool_name:
        result = execute_tool(db, user, tool_name, params)
        return MCPResponse(
            assistant_text=assistant_text or f"Invoked tool {tool_name}",
            tool_name=tool_name,
            tool_result=result,
        )

    return MCPResponse(assistant_text=assistant_text or "I did not detect a tool action.", tool_name=None, tool_result=None)


@router.post("/api/v1/mcp/voice/ask", response_model=MCPVoiceResponse)
async def voice_assistant_query(
    request: MCPVoiceRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    text_request = MCPTextRequest(prompt=request.transcribed_text, restaurant_id=request.restaurant_id)
    response = await natural_language_query(text_request, user=user, db=db)
    return MCPVoiceResponse(
        assistant_text=response.assistant_text,
        tool_name=response.tool_name,
        tool_result=response.tool_result,
        audio_payload=None,
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
