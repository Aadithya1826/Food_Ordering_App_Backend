# Custom MCP Server for Food Ordering App

This repository now includes a custom MCP server layer inside `backend/app/mcp` that connects the existing backend API with a Gemini-powered voice agent.

## Architecture

- `backend/app/main.py`
  - FastAPI application with existing backend routes.
  - Added MCP router from `backend/app/mcp/routes.py`.

- `backend/app/mcp/client.py`
  - Gemini API client for natural language and optional speech generation.
  - Uses `GEMINI_API_KEY` from environment variables.

- `backend/app/mcp/tools.py`
  - Tool registry for backend actions:
    - `list_menu_items`
    - `search_menu_item`
    - `create_order`
    - `get_order_status`
    - `list_restaurants`
  - These tools can be executed directly or via Gemini prompt interpretation.

- `backend/app/mcp/routes.py`
  - `GET /api/v1/mcp/tools`
  - `POST /api/v1/mcp/tools/execute`
  - `POST /api/v1/mcp/natural-language`
  - `POST /api/v1/mcp/voice/ask`
  - `POST /api/v1/mcp/voice/tts`

## Environment variables

This project loads environment variables automatically from a `.env` file when available.

Create a `.env` file in the repository root or copy `.env.example`:

```bash
cp .env.example .env
```

Then set your values:

```bash
DATABASE_URL=postgresql://food_admin:foodadmin%40123@banking-db.cnkegcm24ikf.ap-south-2.rds.amazonaws.com:5432/food_ordering_db
SECRET_KEY=replace-with-a-secure-secret
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
GEMINI_MODEL=gemini-1.5-pro
GEMINI_API_BASE=https://generativelanguage.googleapis.com/v1beta2
```

Replace `YOUR_GEMINI_API_KEY` with your actual key. Do not commit credentials to source control.

## Installation

Add the new dependency and install packages:

```bash
pip install -r requirements.txt
```

## Running the backend

```bash
uvicorn backend.app.main:app --reload --port 8000
```

## Example agent flow

1. User speaks into the customer UI.
2. UI sends the transcribed text to `POST /api/v1/mcp/voice/ask`.
3. The MCP server uses Gemini to interpret the request and choose the best tool.
4. The selected tool runs against the backend database.
5. The MCP server returns structured assistant text and tool results.

## Notes

- The Gemini API key is read from `GEMINI_API_KEY`.
- The voice TTS endpoint is implemented as a Gemini wrapper, but actual audio payload support depends on Gemini speech API availability.
- The MCP server is designed to coexist with your current backend routes and can be extended with new tools as needed.
