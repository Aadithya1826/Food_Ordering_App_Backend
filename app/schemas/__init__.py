# pyrefly: ignore [missing-import]
from pydantic import BaseModel
from typing import Optional

class LoginRequest(BaseModel):
    email: str
    password: str
    role: Optional[str] = None

class SignupRequest(BaseModel):
    name: str
    email: str
    password: str
    role: Optional[str] = None
    restaurant_id: Optional[int] = None

class UserResponse(BaseModel):
    id: int
    email: str
    name: str
    role: str
    restaurant_id: Optional[int]

    class Config:
        from_attributes = True

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

class ManagerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    restaurant_id: Optional[int] = None
    is_active: Optional[bool] = None
    password: Optional[str] = None

# ── Waiter schemas (new — do not affect existing functionality) ────────────────

class WaiterRegisterRequest(BaseModel):
    """Waiter self-registration: name + phone + password. No restaurant chosen here."""
    name: str
    phone: str       # 10-digit Indian mobile, normalized to digits only
    password: str
    confirm_password: str

class WaiterLoginRequest(BaseModel):
    """Waiter login via phone number + password."""
    phone: str
    password: str

class WaiterApproveRequest(BaseModel):
    """Admin assigns a hotel and approves a pending waiter account."""
    restaurant_id: int  # Must be an existing restaurant id (1 or 2)

class WaiterResponse(BaseModel):
    id: int
    name: str
    phone: Optional[str]
    role: str
    restaurant_id: Optional[int]
    restaurant_name: Optional[str]
    is_active: bool
    is_approved: Optional[bool]
    created_at: Optional[str]
