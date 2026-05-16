"""
Tests for centralized authorization in src/api/auth.py.

These tests verify the authorization matrix without a real database by
mocking the session and Subscription/BusinessMember lookups.
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException

from src.api.auth import (
    AuthContext,
    AccessCapabilities,
    _build_capabilities,
    _get_user_id_from_token,
    get_auth_context,
    require_active_member,
    require_subscription,
    require_owner,
    require_manager_or_owner,
)


# ---------------------------------------------------------------------------
# _build_capabilities
# ---------------------------------------------------------------------------

class TestBuildCapabilities:
    def test_owner_with_subscription_has_all_capabilities(self):
        caps = _build_capabilities("owner", subscription_active=True)
        assert caps.can_access_app is True
        assert caps.can_manage_configuration is True
        assert caps.can_manage_products is True
        assert caps.can_manage_finances is True
        assert caps.can_manage_reservations is True

    def test_owner_without_subscription_cannot_access_app(self):
        caps = _build_capabilities("owner", subscription_active=False)
        assert caps.can_access_app is False
        assert caps.can_manage_configuration is False
        assert caps.can_manage_products is False

    def test_manager_can_manage_products_finances_reservations(self):
        caps = _build_capabilities("manager", subscription_active=True)
        assert caps.can_access_app is True
        assert caps.can_manage_configuration is True
        assert caps.can_manage_products is True
        assert caps.can_manage_finances is True
        assert caps.can_manage_reservations is True

    def test_employee_can_manage_reservations_and_view_finances(self):
        caps = _build_capabilities("employee", subscription_active=True)
        assert caps.can_access_app is True
        assert caps.can_manage_configuration is False
        assert caps.can_manage_products is False
        assert caps.can_manage_finances is True
        assert caps.can_manage_reservations is True


# ---------------------------------------------------------------------------
# _get_user_id_from_token
# ---------------------------------------------------------------------------

class TestGetUserIdFromToken:
    def test_missing_bearer_prefix_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _get_user_id_from_token("token-without-bearer")
        assert exc_info.value.status_code == 401

    def test_empty_string_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _get_user_id_from_token("")
        assert exc_info.value.status_code == 401

    def test_invalid_jwt_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            _get_user_id_from_token("Bearer invalid.jwt.token")
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# require_active_member
# ---------------------------------------------------------------------------

class TestRequireActiveMember:
    def _make_auth(self, account_type="owner", member_status="active", role="owner"):
        return AuthContext(
            user_id="u1",
            business_id="b1",
            role=role,
            account_type=account_type,
            member_status=member_status,
            subscription_active=True,
            capabilities=_build_capabilities(role, True),
        )

    def test_owner_passes(self):
        auth = self._make_auth(account_type="owner")
        result = require_active_member(auth)
        assert result.user_id == "u1"

    def test_active_member_passes(self):
        auth = self._make_auth(account_type="member", member_status="active", role="employee")
        result = require_active_member(auth)
        assert result.user_id == "u1"

    def test_inactive_member_raises_403(self):
        auth = self._make_auth(account_type="member", member_status="pending", role="employee")
        with pytest.raises(HTTPException) as exc_info:
            require_active_member(auth)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# require_subscription
# ---------------------------------------------------------------------------

class TestRequireSubscription:
    def _make_auth(self, role="owner", subscription_active=True, member_status="active"):
        return AuthContext(
            user_id="u1",
            business_id="b1",
            role=role,
            account_type="owner" if role == "owner" else "member",
            member_status=member_status,
            subscription_active=subscription_active,
            capabilities=_build_capabilities(role, subscription_active),
        )

    def test_active_subscription_passes(self):
        auth = self._make_auth(subscription_active=True)
        result = require_subscription(auth)
        assert result.user_id == "u1"

    def test_inactive_subscription_raises_403(self):
        auth = self._make_auth(subscription_active=False)
        with pytest.raises(HTTPException) as exc_info:
            require_subscription(auth)
        assert exc_info.value.status_code == 403

    def test_manager_with_active_subscription_passes(self):
        auth = self._make_auth(role="manager", subscription_active=True)
        result = require_subscription(auth)
        assert result.role == "manager"


# ---------------------------------------------------------------------------
# require_owner
# ---------------------------------------------------------------------------

class TestRequireOwner:
    def _make_auth(self, role="owner"):
        return AuthContext(
            user_id="u1",
            business_id="b1",
            role=role,
            account_type="owner" if role == "owner" else "member",
            member_status="active",
            subscription_active=True,
            capabilities=_build_capabilities(role, True),
        )

    def test_owner_passes(self):
        auth = self._make_auth(role="owner")
        result = require_owner(auth)
        assert result.role == "owner"

    def test_manager_raises_403(self):
        auth = self._make_auth(role="manager")
        with pytest.raises(HTTPException) as exc_info:
            require_owner(auth)
        assert exc_info.value.status_code == 403

    def test_employee_raises_403(self):
        auth = self._make_auth(role="employee")
        with pytest.raises(HTTPException) as exc_info:
            require_owner(auth)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# require_manager_or_owner
# ---------------------------------------------------------------------------

class TestRequireManagerOrOwner:
    def _make_auth(self, role="owner"):
        return AuthContext(
            user_id="u1",
            business_id="b1",
            role=role,
            account_type="owner" if role == "owner" else "member",
            member_status="active",
            subscription_active=True,
            capabilities=_build_capabilities(role, True),
        )

    def test_owner_passes(self):
        result = require_manager_or_owner(self._make_auth("owner"))
        assert result.role == "owner"

    def test_manager_passes(self):
        result = require_manager_or_owner(self._make_auth("manager"))
        assert result.role == "manager"

    def test_employee_raises_403(self):
        with pytest.raises(HTTPException) as exc_info:
            require_manager_or_owner(self._make_auth("employee"))
        assert exc_info.value.status_code == 403
