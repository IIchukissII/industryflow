# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The model, reached over an API and chosen by configuration (ADR-0045 dec 3).

Two adapters behind one interface: Claude through the Anthropic SDK, and any OpenAI-compatible
endpoint (Mistral's API, or a model server on the operator's own host). The model only writes; it is
given evidence and calls no tools (ADR-0045 dec 4), so a modest local model can do the job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from config import Config


class ModelDeclined(Exception):
    """The model declined to write the note (a policy refusal, or no text at all)."""


@dataclass(frozen=True)
class Written:
    text: str
    model: str


class NoteWriter(Protocol):
    async def write(self, system: str, prompt: str) -> Written: ...


class AnthropicWriter:
    """Claude through the official SDK. A policy decline on the requested model is retried server-side
    on the model's default fallback; a decline that survives it is reported, never papered over."""

    def __init__(self, config: Config, client: Any = None):
        if client is None:
            import anthropic
            client = anthropic.AsyncAnthropic()
        self._client = client
        self._model = config.model
        self._effort = config.effort

    async def write(self, system: str, prompt: str) -> Written:
        response = await self._client.beta.messages.create(
            model=self._model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self._effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            raise ModelDeclined(f"{response.model} declined (category {category})")
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if not text:
            raise ModelDeclined(f"{response.model} returned no text (stop reason {response.stop_reason})")
        return Written(text, response.model)


class OpenAICompatibleWriter:
    """Any endpoint speaking the OpenAI chat-completions protocol: Mistral's API, or vLLM, Ollama and
    llama.cpp servers on the operator's own host."""

    def __init__(self, config: Config, client: Any = None):
        if client is None:
            import openai
            client = openai.AsyncOpenAI(base_url=config.model_base_url, api_key=config.model_api_key or "none")
        self._client = client
        self._model = config.model

    async def write(self, system: str, prompt: str) -> Written:
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=4000,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        )
        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "").strip() if choice else ""
        if not text:
            reason = choice.finish_reason if choice else "no choices"
            raise ModelDeclined(f"{response.model} returned no text ({reason})")
        return Written(text, response.model)


def writer(config: Config) -> NoteWriter:
    if config.model_provider == "anthropic":
        return AnthropicWriter(config)
    return OpenAICompatibleWriter(config)
