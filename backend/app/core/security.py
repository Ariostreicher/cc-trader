"""JWT + password hashing helpers.

Token-family pattern: each refresh token carries a `family` id; if a refresh
token is used after it has been rotated, the whole family is revoked.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TokenKind = Literal["access", "refresh"]


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception:
        return False


def _expires(kind: TokenKind) -> datetime:
    now = datetime.now(timezone.utc)
    if kind == "access":
        return now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)


def create_token(
    *,
    subject: str | int,
    kind: TokenKind,
    family: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, datetime, str]:
    """Returns (jwt, expires_at, family_id)."""
    family_id = family or secrets.token_urlsafe(16)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "kind": kind,
        "family": family_id,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int(_expires(kind).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    if extra:
        payload.update(extra)
    encoded = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded, _expires(kind), family_id


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as exc:
        raise ValueError(f"invalid token: {exc}") from exc


def generate_reset_token() -> str:
    return secrets.token_urlsafe(48)
