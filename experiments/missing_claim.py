from datetime import datetime, timedelta, timezone

import jwt

from src.auth.jwt_handler import (
    ALGORITHM,
    SECRET_KEY,
    decode_access_token
)


now = datetime.now(timezone.utc)

payload = {
    "sub": "user-123",
    # tenant_id intentionally missing
    "role": "admin",
    "iat": now,
    "exp": now + timedelta(minutes=30)
}

token = jwt.encode(
    payload,
    SECRET_KEY,
    algorithm=ALGORITHM
)

print("\n=== TOKEN WITHOUT TENANT_ID ===\n")
print(token)


try:
    user_context = decode_access_token(token)
    print(user_context)

except jwt.MissingRequiredClaimError as error:
    print("\nMissing claim rejected as expected.")
    print(error)