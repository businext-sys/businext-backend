"""Registro de herramientas del asistente (ADR 0001)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlmodel import Session

from src.api.auth import AuthContext
from src.database.models.profile_model import Profile


@dataclass
class ToolContext:
    auth: AuthContext
    session: Session
    today: date
    conversation_id: str = ""


@dataclass
class ToolResult:
    data: Any
    blocks: list[dict] = field(default_factory=list)


ALL_ROLES = frozenset({"owner", "manager", "employee"})
MANAGEMENT_ROLES = frozenset({"owner", "manager"})
OWNER_ONLY = frozenset({"owner"})


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    status_label: str
    handler: Callable[[ToolContext, dict], ToolResult]
    roles: frozenset[str] = ALL_ROLES

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    TOOLS[tool.name] = tool
    return tool


def own_name(ctx: ToolContext) -> str | None:
    """Nombre visible del usuario: es como aparece en `in_charge` de sus reservas."""
    profile = ctx.session.get(Profile, ctx.auth.user_id)
    return profile.display_name if profile and profile.display_name else None


def tools_for(role: str) -> dict[str, Tool]:
    return {name: tool for name, tool in TOOLS.items() if role in tool.roles}
