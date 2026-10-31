# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The model over an API, by configuration (ADR-0045 dec 3); a decline is reported, not papered over."""

from types import SimpleNamespace as NS

import pytest

from model import AnthropicWriter, ModelDeclined, OpenAICompatibleWriter, writer


class FakeAnthropic:
    def __init__(self, response):
        self.kwargs, self._response = None, response
        self.beta = NS(messages=NS(create=self.create))

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self._response


def _message(stop="end_turn", text="note", details=None):
    return NS(stop_reason=stop, stop_details=details, model="claude-opus-5-5",
              content=[NS(type="thinking", thinking=""), NS(type="text", text=text)])


@pytest.mark.asyncio
async def test_claude_is_asked_with_effort_and_the_default_fallback(config):
    fake = FakeAnthropic(_message())
    out = await AnthropicWriter(config(), client=fake).write("sys", "prompt")
    assert out.text == "note" and out.model == "claude-opus-5-5"
    assert fake.kwargs["model"] == "claude-opus-5-5" and fake.kwargs["output_config"] == {"effort": "medium"}
    assert fake.kwargs["fallbacks"] == "default" and fake.kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in fake.kwargs and "temperature" not in fake.kwargs


@pytest.mark.asyncio
async def test_a_refusal_is_raised(config):
    with pytest.raises(ModelDeclined, match="cyber"):
        await AnthropicWriter(config(), client=FakeAnthropic(_message("refusal", details=NS(category="cyber")))).write("s", "p")


@pytest.mark.asyncio
async def test_an_openai_compatible_endpoint_writes_the_same_note(config):
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return NS(model="mistral-large", choices=[NS(message=NS(content=" note "), finish_reason="stop")])

    fake = NS(chat=NS(completions=NS(create=create)))
    out = await OpenAICompatibleWriter(config(model="mistral-large"), client=fake).write("sys", "prompt")
    assert out.text == "note" and out.model == "mistral-large"
    assert seen["messages"][0] == {"role": "system", "content": "sys"}


def test_the_provider_is_configuration(config):
    assert isinstance(writer(config()), AnthropicWriter)
    assert isinstance(writer(config(model_provider="openai-compatible", model_base_url="http://gpu:8000/v1")),
                      OpenAICompatibleWriter)
