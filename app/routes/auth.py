from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..models.user import User
from ..models.restaurant import Restaurant
from ..utils.auth import verify_password, create_token, hash_password
from ..schemas import LoginRequest, LoginResponse, UserResponse, SignupRequest

router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/api/v1/auth/login")
def login(data: LoginRequest, response: Response, db: Session = Depends(get_db)):

    user = db.query(User).filter(User.email == data.email).first()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid password")

    if data.role:
        if data.role == "SUPER_ADMIN":
            if user.role != "SUPER_ADMIN" or user.restaurant_id is not None:
                raise HTTPException(
                    status_code=403,
                    detail="Super admin login requires a super admin account with no assigned restaurant."
                )
        elif data.role == "HOTEL_ADMIN":
            if user.role != "HOTEL_ADMIN" or user.restaurant_id is None:
                raise HTTPException(
                    status_code=403,
                    detail="Hotel manager login requires a hotel manager account assigned to a restaurant."
                )
        else:
            raise HTTPException(status_code=400, detail="Invalid login role")

    token = create_token(user)

    # Set HTTPOnly cookie for automatic authentication
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=1800,  # 30 minutes
        expires=1800,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax"
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "name": user.name,
            "role": user.role,
            "restaurant_id": user.restaurant_id
        }
    }


@router.post("/api/v1/auth/signup")
def signup(data: SignupRequest, response: Response, db: Session = Depends(get_db)):
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this email already exists")

    if data.role not in ["SUPER_ADMIN", "HOTEL_ADMIN"]:
        raise HTTPException(status_code=400, detail="Invalid signup role")

    if data.role == "HOTEL_ADMIN":
        if data.restaurant_id is None:
            raise HTTPException(status_code=400, detail="Restaurant selection is required for hotel managers")
        restaurant = db.query(Restaurant).filter(Restaurant.id == data.restaurant_id).first()
        if not restaurant:
            raise HTTPException(status_code=404, detail="Selected restaurant not found")
    else:
        data.restaurant_id = None

    # Hash the password
    hashed_password = hash_password(data.password)

    # Create new user
    new_user = User(
        name=data.name,
        email=data.email,
        password_hash=hashed_password,
        role=data.role,
        restaurant_id=data.restaurant_id
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Create token for the new user
    token = create_token(new_user)

    # Set HTTPOnly cookie for automatic authentication
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        max_age=1800,  # 30 minutes
        expires=1800,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax"
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": new_user.id,
            "name": new_user.name,
            "role": new_user.role,
            "restaurant_id": new_user.restaurant_id
        }
    }


@router.post("/api/v1/auth/logout")
def logout(response: Response):
    """
    Logout endpoint - clears the authentication cookie
    """
    response.delete_cookie(key="access_token")
    return {"message": "Logged out successfully"}


@router.get("/api/v1/managers")
def get_managers(db: Session = Depends(get_db)):
    """
    Get all managers (HOTEL_ADMIN users) with their assigned restaurants
    """
    try:
        managers = db.query(User).filter(User.role == "HOTEL_ADMIN").all()
        
        result = []
        for manager in managers:
            restaurant = None
            if manager.restaurant_id:
                restaurant = db.query(Restaurant).filter(Restaurant.id == manager.restaurant_id).first()
            
            result.append({
                "id": manager.id,
                "name": manager.name,
                "email": manager.email,
                "role": manager.role,
                "is_active": manager.is_active,
                "restaurant_id": manager.restaurant_id,
                "restaurant_name": restaurant.name if restaurant else "Not Assigned",
                "restaurant_phone": restaurant.phone if restaurant else None,
                "created_at": manager.created_at.isoformat() if manager.created_at else None
            })
        
        return result
    except Exception as e:
        print(f"Error fetching managers: {str(e)}")
        raise HTTPException(status_code=500, detail="Unable to fetch managers. Please try again.")