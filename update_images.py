from app.db import SessionLocal
from app.models.menu import MenuItem
from app.utils.image_generator import generate_menu_item_image
import time

def run():
    db = SessionLocal()
    try:
        # Find all items that have the old hardcoded builder.io URLs
        items = db.query(MenuItem).filter(MenuItem.image_url.like('%builder.io%')).all()
        if not items:
            print("No items found with hardcoded builder.io URLs.")
            return

        for item in items:
            print(f"Generating image for {item.name} (ID: {item.id})...")
            new_url = generate_menu_item_image(item.id, item.name, item.description)
            if new_url:
                item.image_url = new_url
                db.commit()
                print(f"Updated {item.name} with new local image: {new_url}")
            else:
                print(f"Failed to generate image for {item.name}")
            time.sleep(2)  # Avoid rate limiting
            
        print("\nSuccessfully updated all hardcoded images to local normal images.")
    finally:
        db.close()

if __name__ == "__main__":
    run()
