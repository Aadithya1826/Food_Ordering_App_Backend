from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from ..db import SessionLocal
from ..middleware import get_user_from_token

security = HTTPBearer(auto_error=False)  # Don't auto-error for missing header


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    Dependency function to get the current authenticated user from JWT token
    
    Checks for token in Authorization header first, then in cookies
    
    Args:
        request: FastAPI request object
        credentials: HTTP Bearer token from request header (optional)
        db: Database session
        
    Returns:
        User: Authenticated user object
        
    Raises:
        HTTPException: If token is invalid or user not found
    """
    token = None
    
    # First, try to get token from Authorization header
    if credentials:
        token = credentials.credentials
    # If not in header, try to get from cookie
    elif "access_token" in request.cookies:
        token = request.cookies["access_token"]
    
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user = get_user_from_token(token, db)
    
    return user