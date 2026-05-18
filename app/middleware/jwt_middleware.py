"""
JWT Middleware for role-based access control
- SUPER_ADMIN: Has access to all restaurants
- HOTEL_ADMIN: Has access only to their assigned restaurant
"""
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from jose import jwt, JWTError
from ..models.user import User

SECRET_KEY = "secret"
ALGORITHM = "HS256"


def verify_jwt_token(token: str) -> dict:
    """
    Verify JWT token and extract payload
    
    Args:
        token: JWT token string
        
    Returns:
        dict: Token payload containing user_id, role, restaurant_id
        
    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user_id",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return payload
        
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_user_from_token(token: str, db: Session) -> User:
    """
    Get user object from JWT token
    
    Args:
        token: JWT token string
        db: Database session
        
    Returns:
        User: User object from database
        
    Raises:
        HTTPException: If token is invalid or user not found
    """
    payload = verify_jwt_token(token)
    user_id = payload.get("user_id")
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user


def check_restaurant_access(
    user: User, 
    restaurant_id: int, 
    require_owner: bool = False
) -> bool:
    """
    Check if user has access to a specific restaurant
    
    Authorization rules:
    - SUPER_ADMIN: Can access any restaurant
    - HOTEL_ADMIN: Can only access their assigned restaurant
    
    Args:
        user: User object
        restaurant_id: Restaurant ID to check access for
        require_owner: If True, user must be the owner of the restaurant
        
    Returns:
        bool: True if user has access, raises HTTPException otherwise
        
    Raises:
        HTTPException: If user doesn't have access
    """
    # SUPER_ADMIN has access to all restaurants
    if user.role == "SUPER_ADMIN":
        return True
    
    # HOTEL_ADMIN can only access their own restaurant
    if user.role == "HOTEL_ADMIN":
        if user.restaurant_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Hotel admin must have a restaurant assigned"
            )
        
        if user.restaurant_id != restaurant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: You can only access restaurant {user.restaurant_id}"
            )
        
        return True
    
    # Other roles don't have access
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Access denied: Role '{user.role}' is not authorized"
    )


def get_user_restaurant_filter(user: User) -> int | None:
    """
    Get the restaurant_id filter based on user role
    
    Used for querying data - filters results based on user's access level
    
    Args:
        user: User object
        
    Returns:
        int | None: restaurant_id to filter by, or None if SUPER_ADMIN (no filter)
    """
    if user.role == "SUPER_ADMIN":
        # SUPER_ADMIN sees all restaurants (no filter)
        return None
    
    if user.role == "HOTEL_ADMIN":
        # HOTEL_ADMIN sees only their restaurant
        return user.restaurant_id
    
    # Default: restrict access
    return user.restaurant_id
