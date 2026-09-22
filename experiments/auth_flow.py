from src.auth.authorization import can_upload_document
from src.auth.jwt_handler import (
    create_access_token,
    decode_access_token
)


token = create_access_token(
    user_id="user-123",
    tenant_id="acme",
    role="admin"
)

print("\n=== TOKEN CREATED ===\n")
print(token)


user_context = decode_access_token(token)

print("\n=== AUTHENTICATED USER ===\n")
print(user_context)


can_upload = can_upload_document(
    user_context
)

print("\n=== AUTHORIZATION RESULT ===\n")
print("Can upload document:", can_upload)