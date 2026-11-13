"""Adaptador del LLM del asistente (ADR 0001, actualizado: Claude Haiku 5.5)."""

import json
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
# USD por token; revisar en https://platform.claude.com/docs/en/about-claude/pricing
INPUT_PRICE_PER_TOKEN = 0.10 / 1_000_000
OUTPUT_PRICE_PER_TOKEN = 0.50 / 1_000_000
REQUEST_TIMEOUT_SECONDS = 30
MAX_TOKENS = 2048
REFUSAL_TEXT = "No puedo ayudarte con eso."


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


def _anthropic_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "name": tool["function"]["name"],
            "description": tool["function"]["description"],
            "input_schema": tool["function"]["parameters"],
        }
        for tool in tools
    ]


def _anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system = ""
    converted: list[dict] = []
    for message in messages:
        role = message["role"]
        if role == "system":
            system = message["content"]
        elif role == "tool":
            result = {"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message["content"]}
            previous = converted[-1] if converted else None
            if previous and previous["role"] == "user" and isinstance(previous["content"], list):
                previous["content"].append(result)
            else:
                converted.append({"role": "user", "content": [result]})
        elif message.get("tool_calls"):
            blocks: list[dict] = [{"type": "text", "text": message["content"]}] if message.get("content") else []
            blocks += [
                {
                    "type": "tool_use",
                    "id": call["id"],
                    "name": call["function"]["name"],
                    "input": json.loads(call["function"]["arguments"] or "{}"),
                }
                for call in message["tool_calls"]
            ]
            converted.append({"role": "assistant", "content": blocks})
        elif message.get("content"):
            converted.append({"role": role, "content": message["content"]})
    return system, converted


class AnthropicAssistantLLM:
    def __init__(self, client: anthropic.Anthropic):
        self._client = client

    def stream(self, messages: list[dict], tools: list[dict]) -> Iterator[LLMDelta]:
        system, converted = _anthropic_messages(messages)
        events = self._client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=converted,
            tools=_anthropic_tools(tools),
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            stream=True,
        )
        for event in events:
            if event.type == "message_start":
                yield LLMDelta(prompt_tokens=event.message.usage.input_tokens)
            elif event.type == "content_block_start" and event.content_block.type == "tool_use":
                block = event.content_block
                yield LLMDelta(tool_calls=[ToolCallDelta(index=event.index, id=block.id, name=block.name)])
            elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                yield LLMDelta(text=event.delta.text)
            elif event.type == "content_block_delta" and event.delta.type == "input_json_delta":
                yield LLMDelta(tool_calls=[ToolCallDelta(index=event.index, arguments=event.delta.partial_json)])
            elif event.type == "message_delta":
                yield LLMDelta(completion_tokens=event.usage.output_tokens)
                if event.delta.stop_reason == "refusal":
                    yield LLMDelta(text=REFUSAL_TEXT)


def get_assistant_llm() -> AssistantLLM | None:
    if os.getenv("ASSISTANT_LLM_MODE") == "mock":
        if os.getenv("RENDER"):
            logger.error("ASSISTANT_LLM_MODE=mock ignorado: no se permite en Render (produccion)")
        else:
            from .mock_llm import MockAssistantLLM

            return MockAssistantLLM()
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    return AnthropicAssistantLLM(
        anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1)
    )
