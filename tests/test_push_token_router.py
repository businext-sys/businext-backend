"""
Tests para src/routers/push_token.py (issue #031).

Usa SQLite en memoria para el modelo PushToken y un override de
`get_auth_context` (mismo enfoque que test_auth.py: no se golpea Supabase
ni un JWT real).
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from src.main import app
from src.database.database import get_session
from src.api.auth import AuthContext, AccessCapabilities, get_auth_context
from src.database.models.push_token_model import PushToken  # noqa: F401 (registra la tabla)


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        return session

    def get_auth_context_override():
        return AuthContext(
            user_id="user-1",
            business_id="user-1",
            role="owner",
            account_type="owner",
            capabilities=AccessCapabilities(can_access_app=True),
        )

    app.dependency_overrides[get_session] = get_session_override
    app.dependency_overrides[get_auth_context] = get_auth_context_override
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestRegisterPushToken:
    def test_registers_new_token(self, client: TestClient):
        response = client.post(
            "/users/user-1/push-tokens", json={"token": "ExponentPushToken[abc]"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["user_id"] == "user-1"
        assert data["token"] == "ExponentPushToken[abc]"

    def test_rejects_registering_for_another_user(self, client: TestClient):
        response = client.post(
            "/users/otro-user/push-tokens", json={"token": "ExponentPushToken[abc]"}
        )
        assert response.status_code == 403

    def test_reassigns_existing_token_to_new_user(self, client: TestClient, session: Session):
        # Registrar el token con user-1
        client.post("/users/user-1/push-tokens", json={"token": "ExponentPushToken[dup]"})

        # El mismo token ahora se registra desde otra cuenta autenticada.
        def other_user_auth():
            return AuthContext(
                user_id="user-2",
                business_id="user-2",
                role="owner",
                account_type="owner",
                capabilities=AccessCapabilities(can_access_app=True),
            )

        app.dependency_overrides[get_auth_context] = other_user_auth
        response = client.post(
            "/users/user-2/push-tokens", json={"token": "ExponentPushToken[dup]"}
        )
        assert response.status_code == 201
        assert response.json()["user_id"] == "user-2"

        tokens = session.exec(
            __import__("sqlmodel").select(PushToken).where(
                PushToken.token == "ExponentPushToken[dup]"
            )
        ).all()
        assert len(tokens) == 1
        assert tokens[0].user_id == "user-2"


class TestUnregisterPushToken:
    def test_deletes_token(self, client: TestClient):
        client.post("/users/user-1/push-tokens", json={"token": "ExponentPushToken[del]"})
        response = client.request(
            "DELETE",
            "/users/user-1/push-tokens",
            json={"token": "ExponentPushToken[del]"},
        )
        assert response.status_code == 204

    def test_rejects_deleting_for_another_user(self, client: TestClient):
        response = client.request(
            "DELETE",
            "/users/otro-user/push-tokens",
            json={"token": "ExponentPushToken[whatever]"},
        )
        assert response.status_code == 403
