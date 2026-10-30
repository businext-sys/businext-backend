"""
Preferencias de contenido del radar de tendencias (businext-sys/businext#163).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from src.api.auth import AuthContext, _build_capabilities, get_auth_context
from src.database.database import get_session
from src.database.models.trends_model import ContentPreferences
from src.main import app

OWNER = "owner-1"
AUTH = {"Authorization": "Bearer test"}
DEFAULTS = {
    "enabled": False,
    "networks": ["instagram", "tiktok"],
    "max_per_day": 1,
    "quiet_from": "21:00:00",
    "quiet_to": "09:00:00",
    "timezone": "Europe/Madrid",
    "niche": "barberia",
    "language": "es",
    "country": "ES",
}


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        yield session
    app.dependency_overrides.clear()


@pytest.fixture
def client(session):
    return TestClient(app)


def as_role(role: str) -> AuthContext:
    return AuthContext(
        user_id=OWNER if role == "owner" else "marta",
        business_id=OWNER,
        role=role,
        account_type="owner" if role == "owner" else "member",
        member_status="active",
        subscription_active=True,
        capabilities=_build_capabilities(role, True),
    )


class TestPreferences:
    def test_sin_fila_devuelve_los_valores_por_defecto(self, client):
        app.dependency_overrides[get_auth_context] = lambda: as_role("owner")
        response = client.get("/trends/preferences", headers=AUTH)
        assert response.status_code == 200
        assert response.json() == DEFAULTS

    @pytest.mark.parametrize("role", ["owner", "manager"])
    def test_guarda_y_lee_las_preferencias(self, client, session, role):
        app.dependency_overrides[get_auth_context] = lambda: as_role(role)
        changed = {**DEFAULTS, "enabled": True, "networks": ["tiktok"], "max_per_day": 2, "country": "MX"}
        response = client.put("/trends/preferences", json=changed, headers=AUTH)
        assert response.status_code == 200
        assert response.json() == changed
        assert client.get("/trends/preferences", headers=AUTH).json() == changed
        assert session.get(ContentPreferences, OWNER).networks == '["tiktok"]'

    @pytest.mark.parametrize(
        "change",
        [{"networks": ["youtube"]}, {"max_per_day": 0}, {"timezone": "Europe/Nowhere"}, {"country": "ESP"}],
    )
    def test_rechaza_preferencias_invalidas(self, client, change):
        app.dependency_overrides[get_auth_context] = lambda: as_role("owner")
        assert client.put("/trends/preferences", json={**DEFAULTS, **change}, headers=AUTH).status_code == 422

    def test_employee_recibe_403(self, client):
        app.dependency_overrides[get_auth_context] = lambda: as_role("employee")
        assert client.get("/trends/preferences", headers=AUTH).status_code == 403
        assert client.put("/trends/preferences", json=DEFAULTS, headers=AUTH).status_code == 403

    def test_sin_cabecera_responde_401(self, client):
        assert client.get("/trends/preferences").status_code == 401

    def test_el_openapi_incluye_las_preferencias(self):
        schema = app.openapi()
        assert {"get", "put"} <= schema["paths"]["/trends/preferences"].keys()
        assert "ContentPreferencesPublic" in schema["components"]["schemas"]
