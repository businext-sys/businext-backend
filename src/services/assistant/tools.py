"""Registro de herramientas del asistente (ADR 0001)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlmodel import Session

from src.api.auth import AuthContext


@dataclass
class ToolContext:
    auth: AuthContext
    session: Session
    today: date


@dataclass
class ToolResult:
    data: Any
    blocks: list[dict] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    status_label: str
    handler: Callable[[ToolContext, dict], ToolResult]

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
