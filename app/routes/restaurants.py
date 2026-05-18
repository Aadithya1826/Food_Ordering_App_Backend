from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.restaurant import Restaurant
from ..schemas.restaurant import RestaurantResponse, RestaurantCreateRequest, RestaurantUpdateRequest
from ..utils.dependencies import get_current_user
from ..utils.roles import require_role, require_restaurant_access

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/api/v1/public/restaurants", response_model=list[RestaurantResponse])
def public_restaurants(db: Session = Depends(get_db)):
    """Public restaurant list for signup dropdown."""
    return db.query(Restaurant).all()


@router.get("/api/v1/restaurants", response_model=list[RestaurantResponse])
def list_restaurants(user = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    List all restaurants for SUPER_ADMIN.

    HOTEL_ADMIN does not use this endpoint; they are tied to their own restaurant.
    """
    require_role(user, ["SUPER_ADMIN"])
    
    from ..models.user import User
    
    restaurants = db.query(Restaurant).all()
    result = []
    
    for restaurant in restaurants:
        # Find the manager for this restaurant
        manager = db.query(User).filter(
            User.restaurant_id == restaurant.id,
            User.role == "HOTEL_ADMIN"
        ).first()
        
        manager_name = manager.name if manager else None
        
        result.append(RestaurantResponse(
            id=restaurant.id,
            name=restaurant.name,
            address=restaurant.address,
            phone=restaurant.phone,
            created_at=restaurant.created_at,
            manager_name=manager_name
        ))
    
    return result


@router.post("/api/v1/restaurants", response_model=RestaurantResponse)
def create_restaurant(data: RestaurantCreateRequest, user = Depends(get_current_user), db: Session = Depends(get_db)):
    require_role(user, ["SUPER_ADMIN"])

    existing_restaurant = db.query(Restaurant).filter(Restaurant.name == data.name).first()
    if existing_restaurant:
        raise HTTPException(status_code=400, detail="A restaurant with this name already exists")

    restaurant = Restaurant(
        name=data.name,
        address=data.address,
        phone=data.phone,
    )

    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return restaurant


@router.get("/api/v1/restaurants/{restaurant_id}", response_model=RestaurantResponse)
def get_restaurant(restaurant_id: int, user = Depends(get_current_user), db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    require_restaurant_access(user, restaurant_id)
    return restaurant

@router.patch("/api/v1/restaurants/{restaurant_id}", response_model=RestaurantResponse)
def update_restaurant(restaurant_id: int, data: RestaurantUpdateRequest, user = Depends(get_current_user), db: Session = Depends(get_db)):
    restaurant = db.query(Restaurant).filter(Restaurant.id == restaurant_id).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    require_restaurant_access(user, restaurant_id)

    update_data = data.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(restaurant, key, value)

    db.commit()
    db.refresh(restaurant)
    return restaurant
