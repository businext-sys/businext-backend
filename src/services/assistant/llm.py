"""Adaptador del LLM del asistente (ADR 0001: OpenAI gpt-4o-mini)."""

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

MODEL = "gpt-4o-mini"


@dataclass
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""


@dataclass
class LLMDelta:
    text: str | None = None
    tool_calls: list[ToolCallDelta] = field(default_factory=list)


class AssistantLLM(Protocol):
    def stream(self, messages: list[dict], tools: list[dict]) -> Iterator[LLMDelta]: ...


class OpenAIAssistantLLM:
    def __init__(self, client: OpenAI):
        self._client = client

    def stream(self, messages: list[dict], tools: list[dict]) -> Iterator[LLMDelta]:
        response = self._client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools or None,
            stream=True,
            temperature=0.3,
        )
        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            yield LLMDelta(
                text=delta.content,
                tool_calls=[
                    ToolCallDelta(
                        index=call.index,
                        id=call.id,
                        name=call.function.name if call.function else None,
                        arguments=(call.function.arguments or "") if call.function else "",
                    )
                    for call in delta.tool_calls or []
                ],
            )


def get_assistant_llm() -> AssistantLLM | None:
    api_key = os.getenv("OPENAI_API_KEY", "")
    return OpenAIAssistantLLM(OpenAI(api_key=api_key)) if api_key else None
