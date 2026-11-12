"""
Tests para DELETE /users/me (src/routers/account.py).

SQLite en memoria y override de `get_auth_context`, como en
test_push_token_router.py. Stripe y Supabase Auth se simulan con un
`httpx.MockTransport`: no sale ninguna peticion a la red.
"""
from datetime import date, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from src.api.auth import AccessCapabilities, AuthContext, get_auth_context
from src.database.database import get_session
from src.database.models.assistant_conversation_model import (
    AssistantConversation,
    AssistantMessage,
)
from src.database.models.assistant_usage_model import AssistantUsage
from src.database.models.booking_request_model import BookingRequest
from src.database.models.business_conf_model import BusinessConfiguration
from src.database.models.customer_message_model import CustomerMessage
from src.database.models.finances_model import Finances
from src.database.models.google_business_profile_model import GoogleBusinessProfile
from src.database.models.google_review_model import GoogleReview
from src.database.models.location_model import Location
from src.database.models.member_model import BusinessMember
from src.database.models.product_model import Product
from src.database.models.profile_model import Profile
from src.database.models.push_token_model import PushToken
from src.database.models.reservation_model import Reservation
from src.database.models.subscription_model import Subscription
from src.database.models.trends_model import (
    ContentPreferences,
    ContentSuggestion,
    TrendInsight,
    TrendSnapshot,
)
from src.database.models.voice_agent_model import (
    VoiceAgentConfig,
    VoiceCall,
    VoicePhoneNumber,
)
from src.database.models.weekly_summary_model import WeeklySummary
from src.database.models.working_hours_model import WorkingHours
from src.main import app
from src.services import account_service

OWNER = "owner-1"
EMPLOYEE = "emp-1"
OTHER_OWNER = "owner-2"
SUPABASE_URL = "https://supabase.test"
REAL_ASYNC_CLIENT = httpx.AsyncClient

BUSINESS_MODELS = [
    AssistantConversation,
    AssistantUsage,
    Finances,
    GoogleReview,
    GoogleBusinessProfile,
    ContentSuggestion,
    TrendInsight,
    ContentPreferences,
    CustomerMessage,
    VoiceCall,
    VoiceAgentConfig,
    VoicePhoneNumber,
    WeeklySummary,
    BookingRequest,
    Reservation,
    Product,
    WorkingHours,
    BusinessMember,
    Location,
    BusinessConfiguration,
]


def _seed_business(session: Session, owner_id: str, employee_id: str, suffix: str) -> None:
    now = datetime(2026, 11, 1, 10, 0)
    session.add(BusinessConfiguration(business_id=owner_id, business_name=f"Negocio {suffix}"))
    session.flush()
    location = Location(business_id=owner_id, name="Centro")
    session.add(location)
    session.flush()
    session.add(BusinessMember(business_id=owner_id, member_user_id=employee_id, location_id=location.id))
    reservation = Reservation(
        business_id=owner_id,
        customer_name="Cliente",
        in_charge="Ana",
        reservation_start_date=now,
        reservation_end_date=now,
        time_per_reservation=30,
        status="confirmed",
        service="Corte",
    )
    product = Product(business_id=owner_id, name="Cera", price=10)
    session.add(reservation)
    session.add(product)
    session.flush()
    session.add(Finances(
        business_id=owner_id, concept="Corte", amount=15, type="income", creator="Ana",
        reservation_id=reservation.id, product_id=product.id,
    ))
    session.add(WorkingHours(business_id=owner_id, day_of_week=0, start_time="09:00", end_time="18:00"))
    session.add(WorkingHours(
        business_id=owner_id, day_of_week=1, start_time="09:00", end_time="14:00",
        member_user_id=employee_id,
    ))
    session.add(BookingRequest(
        business_id=owner_id, location_id=location.id, client_name="C", client_email="c@x.com",
        client_phone="600", service="Corte", requested_date=now,
    ))
    profile = GoogleBusinessProfile(business_id=owner_id, source_url="u", google_id=f"g-{suffix}")
    session.add(profile)
    session.flush()
    session.add(GoogleReview(
        business_id=owner_id, profile_id=profile.id, review_id=f"r-{suffix}",
        review_rating=5, review_timestamp=1,
    ))
    insight = TrendInsight(
        business_id=owner_id, day=date(2026, 11, 1), trends_used="[]", analysis="a",
        matched_services="[]", model="m", input_tokens=1, output_tokens=1,
    )
    session.add(insight)
    session.flush()
    session.add(ContentSuggestion(
        business_id=owner_id, insight_id=insight.id, network="instagram", format="reel",
        idea="i", caption_hint="c", publish_at=now, source_trend_ids="[]",
    ))
    session.add(ContentPreferences(business_id=owner_id))
    session.add(CustomerMessage(business_id=owner_id, customer_email="c@x.com", message="m", sent_by=owner_id))
    session.add(VoiceAgentConfig(business_id=owner_id))
    session.add(VoicePhoneNumber(business_id=owner_id, phone_number=f"+3491000000{suffix}"))
    session.add(VoiceCall(business_id=owner_id, call_id=f"call-{suffix}"))
    session.add(WeeklySummary(
        business_id=owner_id, week_start=date(2026, 10, 26), week_end=date(2026, 11, 1),
        narrative="n", kpis="{}",
    ))
    session.add(AssistantUsage(business_id=owner_id, day=date(2026, 11, 1)))
    for user_id in (owner_id, employee_id):
        conversation = AssistantConversation(
            id=f"conv-{user_id}", business_id=owner_id, user_id=user_id, title="t"
        )
        session.add(conversation)
        session.flush()
        session.add(AssistantMessage(id=f"msg-{user_id}", conversation_id=conversation.id, role="user", blocks="[]"))
        session.add(PushToken(user_id=user_id, token=f"ExponentPushToken[{user_id}]"))
        session.add(Profile(id=user_id, display_name=user_id))
    session.add(Subscription(user_id=owner_id, stripe_subscription_id=f"sub_{suffix}", status="active"))


def _count(session: Session, model, **filters) -> int:
    query = select(model)
    for column, value in filters.items():
        query = query.where(getattr(model, column) == value)
    return len(session.exec(query).all())


def _auth(user_id: str, business_id: str, role: str) -> AuthContext:
    return AuthContext(
        user_id=user_id,
        business_id=business_id,
        role=role,
        account_type="owner" if role == "owner" else "member",
        capabilities=AccessCapabilities(can_access_app=True),
    )


class FakeHttp:
    """Registra las peticiones y responde segun `responses[(metodo, host)]`."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.get((request.method, request.url.host), httpx.Response(200, json={}))

    def calls(self, host: str) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests if r.url.host == host]


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_business(session, OWNER, EMPLOYEE, "1")
        _seed_business(session, OTHER_OWNER, "emp-2", "2")
        session.add(TrendSnapshot(
            niche="barberia", language="es", country="ES", source="vidiq",
            day=date(2026, 11, 1), payload="[]",
        ))
        session.commit()
        yield session


@pytest.fixture(name="http")
def http_fixture(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(
        account_service.httpx,
        "AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(fake.handler), **kwargs),
    )
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    return fake


@pytest.fixture(name="client")
def client_fixture(session: Session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(user_id: str, business_id: str, role: str) -> None:
    app.dependency_overrides[get_auth_context] = lambda: _auth(user_id, business_id, role)


def _assert_business_intact(session: Session, business_id: str) -> None:
    for model in BUSINESS_MODELS:
        assert _count(session, model, business_id=business_id) > 0, model.__name__


class TestOwner:
    def test_cancels_stripe_and_deletes_everything(self, client, session, http):
        _login(OWNER, OWNER, "owner")

        response = client.delete("/users/me")

        assert response.status_code == 204
        assert http.calls("api.stripe.com") == ["DELETE /v1/subscriptions/sub_1"]
        assert http.requests[0].headers["authorization"] == "Bearer sk_test_123"
        assert http.calls("supabase.test") == [f"DELETE /auth/v1/admin/users/{OWNER}"]

        for model in BUSINESS_MODELS:
            assert _count(session, model, business_id=OWNER) == 0, model.__name__
        assert _count(session, AssistantMessage) == 2
        assert _count(session, Subscription, user_id=OWNER) == 0
        assert _count(session, Profile, id=OWNER) == 0
        assert _count(session, PushToken, user_id=OWNER) == 0
        assert _count(session, PushToken, user_id=EMPLOYEE) == 0
        assert _count(session, Profile, id=EMPLOYEE) == 1

        _assert_business_intact(session, OTHER_OWNER)
        assert _count(session, Subscription, user_id=OTHER_OWNER) == 1
        assert _count(session, TrendSnapshot) == 1

    def test_without_stripe_key_fails_and_deletes_nothing(self, client, session, http, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY")
        _login(OWNER, OWNER, "owner")

        response = client.delete("/users/me")

        assert response.status_code == 500
        assert http.requests == []
        _assert_business_intact(session, OWNER)
        assert _count(session, Subscription, user_id=OWNER) == 1

    def test_without_billable_subscription_does_not_need_stripe(self, client, session, http, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY")
        sub = session.exec(select(Subscription).where(Subscription.user_id == OWNER)).one()
        sub.status = "canceled"
        session.add(sub)
        session.commit()
        _login(OWNER, OWNER, "owner")

        response = client.delete("/users/me")

        assert response.status_code == 204
        assert http.calls("api.stripe.com") == []
        assert _count(session, BusinessConfiguration, business_id=OWNER) == 0

    def test_stripe_error_fails_and_deletes_nothing(self, client, session, http):
        http.responses[("DELETE", "api.stripe.com")] = httpx.Response(500, json={})
        _login(OWNER, OWNER, "owner")

        response = client.delete("/users/me")

        assert response.status_code == 502
        assert http.calls("supabase.test") == []
        _assert_business_intact(session, OWNER)

    def test_subscription_missing_in_stripe_is_ok(self, client, session, http):
        http.responses[("DELETE", "api.stripe.com")] = httpx.Response(404, json={})
        _login(OWNER, OWNER, "owner")

        assert client.delete("/users/me").status_code == 204
        assert _count(session, Subscription, user_id=OWNER) == 0

    def test_already_canceled_in_stripe_is_ok(self, client, session, http):
        http.responses[("DELETE", "api.stripe.com")] = httpx.Response(400, json={})
        http.responses[("GET", "api.stripe.com")] = httpx.Response(200, json={"status": "canceled"})
        _login(OWNER, OWNER, "owner")

        assert client.delete("/users/me").status_code == 204
        assert _count(session, BusinessConfiguration, business_id=OWNER) == 0


class TestMember:
    def test_deletes_only_own_rows(self, client, session, http):
        _login(EMPLOYEE, OWNER, "employee")

        response = client.delete("/users/me")

        assert response.status_code == 204
        assert http.calls("api.stripe.com") == []
        assert http.calls("supabase.test") == [f"DELETE /auth/v1/admin/users/{EMPLOYEE}"]

        assert _count(session, BusinessMember, member_user_id=EMPLOYEE) == 0
        assert _count(session, PushToken, user_id=EMPLOYEE) == 0
        assert _count(session, AssistantConversation, user_id=EMPLOYEE) == 0
        assert _count(session, AssistantMessage, conversation_id=f"conv-{EMPLOYEE}") == 0
        assert _count(session, WorkingHours, member_user_id=EMPLOYEE) == 0
        assert _count(session, Profile, id=EMPLOYEE) == 0

        assert _count(session, AssistantConversation, user_id=OWNER) == 1
        assert _count(session, PushToken, user_id=OWNER) == 1
        assert _count(session, Profile, id=OWNER) == 1
        assert _count(session, Subscription, user_id=OWNER) == 1
        for model in BUSINESS_MODELS:
            if model is not BusinessMember:
                assert _count(session, model, business_id=OWNER) > 0, model.__name__


class TestAuthAndRetries:
    def test_invalid_token_returns_401(self, client, http):
        response = client.delete("/users/me", headers={"Authorization": "Bearer no-es-un-jwt"})
        assert response.status_code == 401
        assert http.requests == []

    def test_missing_header_is_rejected(self, client, http):
        assert client.delete("/users/me").status_code == 422
        assert http.requests == []

    def test_is_idempotent(self, client, session, http):
        _login(OWNER, OWNER, "owner")
        assert client.delete("/users/me").status_code == 204

        http.responses[("DELETE", "supabase.test")] = httpx.Response(404, json={"msg": "User not found"})
        assert client.delete("/users/me").status_code == 204
        assert http.calls("api.stripe.com") == ["DELETE /v1/subscriptions/sub_1"]

    def test_auth_failure_after_deleting_data_returns_502_and_retry_works(self, client, session, http):
        http.responses[("DELETE", "supabase.test")] = httpx.Response(500, json={})
        _login(OWNER, OWNER, "owner")

        assert client.delete("/users/me").status_code == 502
        assert _count(session, BusinessConfiguration, business_id=OWNER) == 0

        del http.responses[("DELETE", "supabase.test")]
        assert client.delete("/users/me").status_code == 204
        assert http.calls("supabase.test") == [f"DELETE /auth/v1/admin/users/{OWNER}"] * 2
