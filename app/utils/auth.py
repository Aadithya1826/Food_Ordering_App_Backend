import os
import bcrypt

from jose import jwt
from datetime import datetime, timedelta
from fastapi import HTTPException

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable is not set. A secure key is required for production.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

import hashlib

def hash_password(password: str):
    """
    Hashes password using SHA-256 security.
    """
    password = password.strip()
    if not password:
        raise HTTPException(status_code=400, detail="Password cannot be empty")
    return hashlib.sha256(password.encode('utf-8')).hexdigest()


def verify_password(plain, hashed):
    """
    Verifies plain password against stored hash.
    Supports SHA-256 and backward-compatible bcrypt hashes.
    """
    try:
        if not plain or not hashed:
            return False
        plain = plain.strip()

        # Check SHA-256
        sha256_hash = hashlib.sha256(plain.encode('utf-8')).hexdigest()
        if sha256_hash.lower() == hashed.lower():
            return True

        # Backward compatibility for existing bcrypt hashes
        if hashed.startswith("$2b$") or hashed.startswith("$2a$"):
            return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))

        return False
    except Exception:
        return False



import uuid

def create_rider_token(rider_id: int):
    """
    Generates a secure, signed JWT access token for a Rider.
    Required claims: sub, role, type, iat, nbf, exp, jti.
    Expires in 60 minutes.
    """
    now = datetime.utcnow()
    expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = now + expires_delta
    jti = str(uuid.uuid4())

    data = {
        "sub": str(rider_id),
        "user_id": str(rider_id),
        "role": "RIDER",
        "account_type": "RIDER",
        "type": "access",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": expire,
        "jti": jti
    }

    token = jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)
    return {
        "access_token": token,
        "token": token,
        "token_type": "bearer",
        "jti": jti,
        "expires_at": expire,
        "expires_in": int(expires_delta.total_seconds())
    }


def create_token(user, account_type: str = "USER"):
    now = datetime.utcnow()
    expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = now + expires_delta
    jti = str(uuid.uuid4())

    data = {
        "user_id": str(user.id),
        "role": getattr(user, "role", "USER"),
        "restaurant_id": getattr(user, "restaurant_id", None),
        "account_type": account_type,
        "sub": str(user.id),
        "type": "access",
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": expire,
        "jti": jti
    }

    return jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)