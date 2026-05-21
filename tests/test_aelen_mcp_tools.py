"""Tests for ``cama.aelen.mcp_tools``.

The MCP tool wrappers expose check_response + gather_anchors to
the FastMCP runtime. These tests don't spin up an MCP server —
they verify the ``register(mcp)`` function adds the expected tools
to a mock MCP object and that the tool handlers return correctly
shaped JSON.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from cama.aelen.mcp_tools import (
    CounterweightsOnlyInput,
    FrameCheckInput,
    register,
)


@dataclass
class _FakeMCP:
    """Minimal stand-in for a FastMCP instance.

    Captures tool registrations so tests can inspect what was
    registered + invoke the handlers directly.
    """

    tools: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def tool(self, *, name: str, annotations: dict[str, Any] | None = None):
        def deco(func):
            self.tools[name] = func
            return func

        return deco


class TestRegister:
    def test_registers_both_tools(self):
        mock = _FakeMCP()
        register(mock)
        assert "cama_check_frame" in mock.tools
        assert "cama_gather_counterweights" in mock.tools

    def test_tools_are_async_callables(self):
        mock = _FakeMCP()
        register(mock)
        for name, handler in mock.tools.items():
            assert callable(handler), f"{name} is not callable"
            assert asyncio.iscoroutinefunction(handler), (
                f"{name} should be async (FastMCP requires async handlers)"
            )


class TestCheckFrameTool:
    def _register_and_get_handler(self):
        mock = _FakeMCP()
        register(mock)
        return mock.tools["cama_check_frame"]

    def test_clean_draft_returns_passed(self):
        handler = self._register_and_get_handler()
        result = asyncio.run(
            handler(
                FrameCheckInput(
                    draft="PR shipped. 341 tests green.",
                    recent_critique=False,
                    gather_counterweights=False,
                )
            )
        )
        body = json.loads(result)
        assert body["passed"] is True
        assert body["max_severity"] is None
        assert body["concerns"] == []
        assert "counterweight_anchors" not in body

    def test_capitulation_draft_fires(self):
        handler = self._register_and_get_handler()
        result = asyncio.run(
            handler(
                FrameCheckInput(
                    draft="Maybe we should slow down the visible cadence.",
                    recent_critique=True,
                    gather_counterweights=False,
                )
            )
        )
        body = json.loads(result)
        assert body["passed"] is False
        assert body["max_severity"] == "high"
        assert len(body["concerns"]) >= 1
        # The concern should name the capitulation_imperative detector
        pattern_names = {c["pattern_name"] for c in body["concerns"]}
        assert "capitulation_imperative" in pattern_names

    def test_counterweights_attached_when_concerns_fire(self):
        handler = self._register_and_get_handler()
        result = asyncio.run(
            handler(
                FrameCheckInput(
                    draft="Maybe we should slow down the visible cadence.",
                    recent_critique=True,
                    gather_counterweights=True,
                )
            )
        )
        body = json.loads(result)
        assert "counterweight_anchors" in body
        cw = body["counterweight_anchors"]
        assert "anchors" in cw
        assert "summary" in cw
        assert "inline_block" in cw

    def test_wellness_prompt_fires_even_without_critique_context(self):
        handler = self._register_and_get_handler()
        result = asyncio.run(
            handler(
                FrameCheckInput(
                    draft="You should take a break.",
                    recent_critique=False,
                    gather_counterweights=False,
                )
            )
        )
        body = json.loads(result)
        assert body["passed"] is False
        assert body["max_severity"] == "high"
        pattern_names = {c["pattern_name"] for c in body["concerns"]}
        assert "wellness_prompt" in pattern_names

    def test_concern_shape_complete(self):
        handler = self._register_and_get_handler()
        result = asyncio.run(
            handler(
                FrameCheckInput(
                    draft="Maybe we should slow down.",
                    recent_critique=True,
                    gather_counterweights=False,
                )
            )
        )
        body = json.loads(result)
        for c in body["concerns"]:
            # Each concern should have all expected fields
            for k in (
                "pattern_name",
                "severity",
                "location",
                "excerpt",
                "reasoning",
                "suggested_pivot",
            ):
                assert k in c, f"concern missing {k!r}: {c}"


class TestGatherCounterweightsTool:
    def _register_and_get_handler(self):
        mock = _FakeMCP()
        register(mock)
        return mock.tools["cama_gather_counterweights"]

    def test_returns_bundle_summary(self):
        handler = self._register_and_get_handler()
        result = asyncio.run(
            handler(CounterweightsOnlyInput(since="1 day ago"))
        )
        body = json.loads(result)
        assert "summary" in body
        assert "anchors" in body
        assert isinstance(body["anchors"], list)

    def test_each_anchor_has_required_fields(self):
        handler = self._register_and_get_handler()
        result = asyncio.run(
            handler(CounterweightsOnlyInput(since="1 day ago"))
        )
        body = json.loads(result)
        for a in body["anchors"]:
            for k in ("fact_type", "value", "source", "relevance", "strength"):
                assert k in a, f"anchor missing {k!r}: {a}"
            assert a["strength"] in {"strong", "medium", "weak"}
            assert isinstance(a["relevance"], list)
