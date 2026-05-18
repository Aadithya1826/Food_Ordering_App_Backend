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
