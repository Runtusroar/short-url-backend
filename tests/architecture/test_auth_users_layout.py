from app.features.auth.router import router as auth_router
from app.features.auth.schemas import Token
from app.features.auth.service import authenticate_user, issue_token
from app.features.users.router import router as users_router
from app.features.users.schemas import UserCreate, UserResponse


def test_auth_and_users_feature_interfaces():
    assert auth_router.prefix == "/api/auth"
    assert users_router.prefix == "/api/admin/users"
    assert Token(access_token="x").token_type == "bearer"
    assert callable(authenticate_user)
    assert callable(issue_token)
    assert UserCreate.model_fields["username"].is_required()
    assert UserResponse.model_config["from_attributes"] is True
