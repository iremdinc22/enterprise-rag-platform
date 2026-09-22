import os
from datetime import datetime, timedelta, timezone

import jwt
from dotenv import load_dotenv

from src.auth.models import UserContext


load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = "HS256"

if not SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY environment variable is not set"
    )


def create_access_token(
    user_id,
    tenant_id,
    role,
    expires_minutes=30
):
    now = datetime.now(timezone.utc)

    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes)
    }

    return jwt.encode(
        payload,
        SECRET_KEY,
        algorithm=ALGORITHM
    )


def decode_access_token(token):
    payload = jwt.decode(
        token,
        SECRET_KEY,
        algorithms=[ALGORITHM],
        options={
            "require": [
                "sub",
                "tenant_id",
                "role",
                "iat",
                "exp"
            ]
        }
    )

    return UserContext(
        user_id=payload["sub"],
        tenant_id=payload["tenant_id"],
        role=payload["role"]
    )