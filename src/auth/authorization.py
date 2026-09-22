from src.auth.models import Role, UserContext


def can_upload_document(
    user_context: UserContext
):
    return user_context.role == Role.ADMIN