"""
Tender AI Platform - Authentication Utilities
JWT token management and password hashing
"""

import jwt
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal
from fastapi import HTTPException, Depends, Request
from app.core.config import settings

# JWT Configuration
JWT_SECRET = getattr(settings, 'JWT_SECRET', 'tender-ai-secret-key-change-in-production')
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24


def hash_password(password: str) -> str:
    """Hash password with SHA-256 + salt"""
    salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{hashed}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against hash"""
    try:
        salt, hashed = password_hash.split(":")
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() == hashed
    except ValueError:
        return False


def create_access_token(
    user_id: str,
    user_type: Literal["admin", "client"],
    extra_claims: dict = None
) -> str:
    """Create JWT access token"""
    payload = {
        "sub": user_id,
        "type": user_type,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate JWT token"""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_token_from_header(request: Request) -> str:
    """Extract Bearer token from Authorization header"""
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization token")
    return auth[7:]


def require_admin(request: Request) -> dict:
    """Dependency: require valid admin token"""
    token = get_token_from_header(request)
    payload = decode_token(token)
    if payload.get("type") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return payload


def require_client(request: Request) -> dict:
    """Dependency: require valid client token"""
    token = get_token_from_header(request)
    payload = decode_token(token)
    if payload.get("type") != "client":
        raise HTTPException(status_code=403, detail="Client access required")
    return payload
