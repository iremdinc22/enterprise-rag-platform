from pydantic import ValidationError

from src.auth.authorization import can_upload_document
from src.auth.models import UserContext


employee = UserContext(
    user_id="user-123",
    tenant_id="acme",
    role="employee"
)

admin = UserContext(
    user_id="user-456",
    tenant_id="acme",
    role="admin"
)


print(
    "Employee can upload:",
    can_upload_document(employee)
)

print(
    "Admin can upload:",
    can_upload_document(admin)
)


try:
    invalid_user = UserContext(
        user_id="user-789",
        tenant_id="acme",
        role="patates"
    )

    print(invalid_user)

except ValidationError as error:
    print("\nInvalid role rejected as expected.")
    print(error)