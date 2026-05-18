from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.menu import MenuItem, MenuCategory
from ..schemas.menu import MenuItemCreate, MenuItemResponse, MenuItemUpdate, MenuCategoryResponse
from ..utils.dependencies import get_current_user
from ..utils.roles import require_role, resolve_restaurant_id

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# GET categories
@router.get("/api/v1/menu/categories", response_model=list[MenuCategoryResponse])
def get_categories(
    restaurant_id: int | None = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    restaurant_id = resolve_restaurant_id(user, restaurant_id)

    query = db.query(MenuCategory)
    if restaurant_id is not None:
        query = query.filter(MenuCategory.restaurant_id == restaurant_id)

    return query.all()

# GET items
@router.get("/api/v1/menu/items", response_model=list[MenuItemResponse])
def get_items(
    restaurant_id: int | None = None,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get menu items with role-based access control

    - SUPER_ADMIN: Sees items from the selected restaurant or all restaurants when no restaurant_id is provided
    - HOTEL_ADMIN: Sees items only from their restaurant
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])
    restaurant_id = resolve_restaurant_id(user, restaurant_id)

    query = db.query(MenuItem)
    if restaurant_id is not None:
        query = query.filter(MenuItem.restaurant_id == restaurant_id)

    return query.all()

def generate_and_update_image(item_id: int, item_name: str, item_description: str):
    db = SessionLocal()
    try:
        from ..utils.image_generator import generate_menu_item_image
        image_url = generate_menu_item_image(item_id, item_name, item_description)
        if image_url:
            item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
            if item:
                item.image_url = image_url
                db.commit()
    except Exception as e:
        print(f"Background task failed: {e}")
    finally:
        db.close()

# POST item
@router.post("/api/v1/menu/items", response_model=MenuItemResponse)
def create_item(
    data: MenuItemCreate,
    background_tasks: BackgroundTasks,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    item_data = data.model_dump()
    if user.role == "HOTEL_ADMIN":
        if item_data.get("restaurant_id") is not None and item_data["restaurant_id"] != user.restaurant_id:
            raise HTTPException(
                status_code=403,
                detail=f"Hotel admin can only create items for restaurant {user.restaurant_id}"
            )
        item_data["restaurant_id"] = user.restaurant_id
    else:
        if not item_data.get("restaurant_id"):
            raise HTTPException(status_code=400, detail="restaurant_id is required for SUPER_ADMIN")

    item = MenuItem(**item_data)
    db.add(item)
    db.commit()
    db.refresh(item)
    
    # Enqueue background task for image generation
    background_tasks.add_task(generate_and_update_image, item.id, item.name, item.description)
    
    return item

# PATCH item - with role-based validation
@router.patch("/api/v1/menu/items/{item_id}", response_model=MenuItemResponse)
def update_item(
    item_id: int,
    data: MenuItemUpdate,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update a menu item with proper authorization check

    - SUPER_ADMIN: Can update items from any restaurant
    - HOTEL_ADMIN: Can only update items from their restaurant
    """
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if user.role == "HOTEL_ADMIN" and item.restaurant_id != user.restaurant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Access denied: This item belongs to restaurant {item.restaurant_id}, you can only access restaurant {user.restaurant_id}"
        )

    update_data = data.model_dump(exclude_unset=True)
    if user.role == "HOTEL_ADMIN" and update_data.get("restaurant_id") is not None and update_data["restaurant_id"] != user.restaurant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Hotel admin cannot move items to restaurant {update_data['restaurant_id']}"
        )

    for key, value in update_data.items():
        setattr(item, key, value)

    db.commit()
    db.refresh(item)
    return item

# POST generate image for existing item
@router.post("/api/v1/menu/items/{item_id}/generate-image", response_model=MenuItemResponse)
def generate_image_for_item(
    item_id: int,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if user.role == "HOTEL_ADMIN" and item.restaurant_id != user.restaurant_id:
        raise HTTPException(
            status_code=403,
            detail="Access denied"
        )
        
    from ..utils.image_generator import generate_menu_item_image
    image_url = generate_menu_item_image(item.id, item.name, item.description)
    
    if image_url:
        item.image_url = image_url
        db.commit()
        db.refresh(item)
    else:
        raise HTTPException(status_code=500, detail="Failed to generate image")
        
    return item

# DELETE item
@router.delete("/api/v1/menu/items/{item_id}", status_code=204)
def delete_item(
    item_id: int,
    user = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_role(user, ["HOTEL_ADMIN", "SUPER_ADMIN"])

    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")

    if user.role == "HOTEL_ADMIN" and item.restaurant_id != user.restaurant_id:
        raise HTTPException(
            status_code=403,
            detail="Access denied"
        )

    db.delete(item)
    db.commit()
    return None