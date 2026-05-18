import os
from google import genai
from PIL import Image
from io import BytesIO
import time
import requests
import random
import urllib.parse
import threading

# Global lock to prevent rate-limiting when multiple images are generated simultaneously
_generation_lock = threading.Lock()

def save_image_bytes(item_id: int, image_bytes: bytes) -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    static_dir = os.path.join(base_dir, "static", "images")
    os.makedirs(static_dir, exist_ok=True)
    
    filename = f"item_{item_id}_{int(time.time())}_{random.randint(1,1000)}.jpeg"
    filepath = os.path.join(static_dir, filename)
    
    image = Image.open(BytesIO(image_bytes))
    if image.mode != 'RGB':
        image = image.convert('RGB')
    image.save(filepath, "JPEG")
    
    print(f"Image successfully saved to {filepath}")
    return f"/static/images/{filename}"

def generate_menu_item_image(item_id: int, item_name: str, item_description: str = "") -> str | None:
    with _generation_lock:
        api_key = os.getenv("GEMINI_API_KEY")
    
        if api_key:
            try:
                client = genai.Client(api_key=api_key)
                prompt = f"Professional food photography of a dish called {item_name}. {item_description or ''}. Beautifully plated, high quality, appealing, 4k resolution, appetizing, well lit, food photography."
                
                print(f"Generating image for {item_name} with Gemini Imagen 4...")
                result = client.models.generate_images(
                    model='imagen-4.0-fast-generate-001',
                    prompt=prompt,
                    config=dict(
                        number_of_images=1,
                        output_mime_type="image/jpeg",
                        aspect_ratio="1:1"
                    )
                )
                
                if result.generated_images:
                    image_bytes = result.generated_images[0].image.image_bytes
                    return save_image_bytes(item_id, image_bytes)
            except Exception as e:
                print(f"Gemini API failed or not available for {item_name}: {e}")
                print("Falling back to demo placeholder image...")
        else:
            print("No Gemini API key found. Using fallback image.")
        
        # Fallback mechanism for free tier keys or errors
        # Try Pollinations AI with retries for rate limits
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"Using dynamic fallback image generation for {item_name} (Attempt {attempt+1})...")
                prompt_text = f"Professional food photography of {item_name}, appetizing, beautiful plating, high quality"
                encoded_prompt = urllib.parse.quote(prompt_text)
                seed = random.randint(1, 1000000)
                url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=600&height=600&nologo=true&seed={seed}"
                
                response = requests.get(url, timeout=30)
                if response.status_code == 200:
                    return save_image_bytes(item_id, response.content)
                elif response.status_code == 429:
                    print("Rate limited by Pollinations AI. Waiting before retry...")
                    time.sleep(3 * (attempt + 1)) # Exponential backoff
                else:
                    print(f"Fallback generation failed with status code {response.status_code}")
                    break # Break on non-429 errors
            except Exception as e:
                print(f"Fallback image generation failed: {e}")
                time.sleep(2)
        
        # Secondary Fallback: Dummy image placeholder with item name if generation completely fails
        try:
            print("Using secondary generic fallback image...")
            dummy_text = urllib.parse.quote(item_name)
            response = requests.get(f"https://dummyimage.com/600x600/f3f4f6/111827.png&text={dummy_text}", timeout=10)
            if response.status_code == 200:
                return save_image_bytes(item_id, response.content)
        except Exception as e:
            print(f"Secondary fallback failed: {e}")
            
        return None
