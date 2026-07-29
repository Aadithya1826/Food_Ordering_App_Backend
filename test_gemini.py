import os
import json
import asyncio
from dotenv import load_dotenv

load_dotenv(r"d:\TECH WIZARD FOLDER-INTERN\Restaurant app-web\backend\.env")
API_KEY = os.getenv("GEMINI_API_KEY")

import google.generativeai as genai

async def test_gemini():
    genai.configure(api_key=API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    import sys
    sys.path.append(r"d:\TECH WIZARD FOLDER-INTERN\Restaurant app-web\backend")
    from app.services.prompt_builder import build_voice_agent_prompt
    
    context = {
        "currentPage": "/dine-in",
        "language": "English",
        "cart": [],
        "tableNumber": "10",
        "menuCategories": [],
        "menuItems": []
    }
    system_prompt = build_voice_agent_prompt(context)
    
    contents = [{"role": "user", "parts": ["singapore noodles and veg noodles and veg chinese capsules."]}]
    
    try:
        response = model.generate_content(
            contents=contents,
            generation_config=genai.types.GenerationConfig(
                temperature=0.1,
                candidate_count=1,
                max_output_tokens=250,
                response_mime_type="application/json",
            )
        )
        print("Raw Gemini Response:")
        print(response.text)
    except Exception as e:
        print("Error calling Gemini:", str(e))

if __name__ == "__main__":
    asyncio.run(test_gemini())
