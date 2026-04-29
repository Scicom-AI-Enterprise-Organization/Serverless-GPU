"""User auth: register, login, session-token middleware.

- Passwords: bcrypt-hashed in Postgres `users.password_hash`.
- Sessions: random opaque tokens. Stored in Redis as `session:{token}` -> user_id
  with TTL (default 7d). Logout = DEL key.
- Bearer tokens in `Authorization: Bearer <token>` resolve to a User row.
"""
from __future__ import annotations

import os
import secrets
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import User, get_session, get_user_by_id

SESSION_TTL_S = 7 * 24 * 3600  # 7 days


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


async def create_session(rdb, user_id: int) -> str:
    token = new_session_token()
    await rdb.set(f"session:{token}", str(user_id), ex=SESSION_TTL_S)
    return token


async def revoke_session(rdb, token: str) -> None:
    await rdb.delete(f"session:{token}")


async def resolve_session(rdb, token: str) -> Optional[int]:
    raw = await rdb.get(f"session:{token}")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    """FastAPI dep: resolves Authorization: Bearer <token> -> User. 401 otherwise.

    When AUTH_DISABLED=1, every request acts as the seeded admin user — no
    token required. Intended for demos / dev where security is irrelevant.
    """
    if os.environ.get("AUTH_DISABLED") == "1":
        result = await session.execute(select(User).where(User.is_admin == True).limit(1))
        admin = result.scalar_one_or_none()
        if admin is None:
            raise HTTPException(
                status_code=503,
                detail={"error": "AUTH_DISABLED set but no admin user exists; set ADMIN_USERNAME + ADMIN_PASSWORD env"},
            )
        return admin

    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "missing or malformed Authorization header"})
    token = header[len("Bearer "):].strip()

    rdb = request.app.state.redis
    user_id = await resolve_session(rdb, token)
    if user_id is None:
        raise HTTPException(status_code=401, detail={"error": "invalid or expired session"})

    user = await get_user_by_id(session, user_id)
    if user is None:
        # Session points at a deleted user; clean up.
        await revoke_session(rdb, token)
        raise HTTPException(status_code=401, detail={"error": "user no longer exists"})
    return user
