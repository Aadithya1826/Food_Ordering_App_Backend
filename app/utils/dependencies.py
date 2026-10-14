# pyrefly: ignore [missing-import]
from fastapi import Depends, HTTPException, Request
# pyrefly: ignore [missing-import]
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
# pyrefly: ignore [missing-import]
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

def get_current_rider(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    token = None
    
    # First, try to get token from Authorization header
    if credentials:
        token = credentials.credentials
    # If not in header, try to get from cookie
    elif "access_token" in request.cookies:
        token = request.cookies["access_token"]
    
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    from ..middleware.jwt_middleware import verify_jwt_token
    payload = verify_jwt_token(token)
    
    # Validate token type
    token_type = payload.get("type")
    if token_type and token_type != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    
    # Validate role
    role = payload.get("role")
    account_type = payload.get("account_type")
    if role != "RIDER" and account_type != "RIDER":
        raise HTTPException(status_code=403, detail="Rider access required")
        
    rider_id = payload.get("sub") or payload.get("user_id")
    if not rider_id:
        raise HTTPException(status_code=401, detail="Invalid rider token")
        
    # Check session revocation via jti
    jti = payload.get("jti")
    if jti:
        from ..models.delivery import RiderSession
        session_record = db.query(RiderSession).filter(RiderSession.jti == jti).first()
        if session_record and session_record.is_revoked:
            raise HTTPException(status_code=401, detail="Session has been revoked. Please log in again.")
        
    from ..models.delivery import DeliveryPartner
    try:
        rider_pk = int(rider_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid rider ID in token")
        
    rider = db.query(DeliveryPartner).filter(DeliveryPartner.id == rider_pk).first()
    
    if not rider:
        raise HTTPException(status_code=401, detail="Rider not found")
        
    if not rider.is_active:
        raise HTTPException(status_code=401, detail="Rider account is inactive or disabled")
        
    rider._token_jti = jti
    return rider

def get_current_customer(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    Dependency function to get the current authenticated customer from JWT token
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
    
    from ..middleware.jwt_middleware import verify_jwt_token
    payload = verify_jwt_token(token)
    
    account_type = payload.get("account_type")
    role = payload.get("role")
    
    # If the token explicitly declares it belongs to a different app (e.g. RIDER), reject it.
    # If the token is old and lacks these fields, we allow it to proceed to the DB lookup.
    if account_type and account_type not in ["CUSTOMER", "USER"]:
        raise HTTPException(status_code=403, detail="Customer access required (invalid account_type)")
    if role and role not in ["CUSTOMER", "USER"]:
        raise HTTPException(status_code=403, detail="Customer access required (invalid role)")
        
    customer_id_str = str(payload.get("user_id") or payload.get("sub") or payload.get("id"))
    print(f"DEBUG get_current_customer: extracted customer_id='{customer_id_str}' from payload={payload}")
    if not customer_id_str:
        raise HTTPException(status_code=401, detail="Invalid customer token")
        
    from ..models.customer import Customer
    if customer_id_str.startswith("+") or not customer_id_str.isdigit():
        customer = db.query(Customer).filter(Customer.phone == customer_id_str).first()
    else:
        customer = db.query(Customer).filter(Customer.id == int(customer_id_str)).first()
        
    print(f"DEBUG get_current_customer: db lookup returned {customer.id if customer else None}")
    
    if not customer:
        raise HTTPException(status_code=401, detail="Customer not found")
        
    return customer