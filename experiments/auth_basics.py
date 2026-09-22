import base64
import json

import jwt

from src.auth.jwt_handler import (
    create_access_token,
    decode_access_token
)


token = create_access_token(
    user_id="user-123",
    tenant_id="acme",
    role="employee"
)

print("\n=== ORIGINAL TOKEN ===\n")
print(token)


header, payload, signature = token.split(".")

payload_bytes = base64.urlsafe_b64decode(
    payload + "=" * (-len(payload) % 4)
)

payload_data = json.loads(payload_bytes)

print("\n=== ORIGINAL PAYLOAD ===\n")
print(payload_data)


payload_data["role"] = "admin"

tampered_payload = base64.urlsafe_b64encode(
    json.dumps(
        payload_data,
        separators=(",", ":")
    ).encode()
).rstrip(b"=").decode()


tampered_token = (
    f"{header}.{tampered_payload}.{signature}"
)

print("\n=== TAMPERED TOKEN ===\n")
print(tampered_token)


try:
    user_context = decode_access_token(
        tampered_token
    )

    print("\n=== USER CONTEXT ===\n")
    print(user_context)

except jwt.InvalidSignatureError:
    print(
        "\nTampered token rejected as expected."
    )