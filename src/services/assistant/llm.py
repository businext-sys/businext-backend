"""Adaptador del LLM del asistente (ADR 0001: OpenAI gpt-4o-mini)."""

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"
# Precios del ADR 0001 (USD por token); revisar en https://openai.com/api/pricing
INPUT_PRICE_PER_TOKEN = 0.15 / 1_000_000
OUTPUT_PRICE_PER_TOKEN = 0.60 / 1_000_000
REQUEST_TIMEOUT_SECONDS = 30


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
    prompt_tokens: int = 0
    completion_tokens: int = 0


def cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return prompt_tokens * INPUT_PRICE_PER_TOKEN + completion_tokens * OUTPUT_PRICE_PER_TOKEN


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
            stream_options={"include_usage": True},
            temperature=0.3,
        )
        for chunk in response:
            if chunk.usage:
                yield LLMDelta(
                    prompt_tokens=chunk.usage.prompt_tokens,
                    completion_tokens=chunk.usage.completion_tokens,
                )
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
    if os.getenv("ASSISTANT_LLM_MODE") == "mock":
        if os.getenv("RENDER"):
            logger.error("ASSISTANT_LLM_MODE=mock ignorado: no se permite en Render (produccion)")
        else:
            from .mock_llm import MockAssistantLLM

            return MockAssistantLLM()
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    return OpenAIAssistantLLM(OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1))
