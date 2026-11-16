from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pydantic import ValidationError

from src.auth.jwt_handler import decode_access_token
from src.auth.models import UserContext

bearer = HTTPBearer(auto_error=False)


def authenticated_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> UserContext:
    try:
        if credentials is None:
            raise ValueError("Missing credentials")
        user = decode_access_token(credentials.credentials)
        if not user.user_id.strip() or not user.tenant_id.strip():
            raise ValueError("Empty identity")
        return user
    except (InvalidTokenError, ValidationError, ValueError, TypeError):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def get_pipeline():
    # Loaded at startup in production; replaceable without model downloads in tests.
    from src.rag.pipeline import run_rag

    return run_rag
