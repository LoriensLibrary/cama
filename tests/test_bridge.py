"""Tests for ``mcp_sections.bridge``.

Covers:
- cama_write_file allowlist enforcement (inside vs outside)
- ``..`` traversal refusal
- CAMA_BRIDGE_WRITE_ALLOWLIST env var override
- MCP tool registration carries the honest destructiveHint / openWorldHint flags
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from mcp_sections import bridge


@dataclass
class _FakeMCP:
    """Minimal stand-in for a FastMCP instance.

    Captures tool registrations so tests can inspect what was registered.
    """

    tools: dict[str, Callable[..., Any]] = field(default_factory=dict)
    annotations: dict[str, dict[str, Any]] = field(default_factory=dict)

    def tool(self, *, name: str, annotations: dict[str, Any] | None = None):
        def deco(func):
            self.tools[name] = func
            self.annotations[name] = annotations or {}
            return func

        return deco


class TestRegisterAnnotations:
    """The MCP annotation metadata must honestly reflect tool behavior."""

    def _registered(self) -> _FakeMCP:
        mock = _FakeMCP()
        bridge.register(mock)
        return mock

    def test_all_three_tools_registered(self):
        mock = self._registered()
        assert set(mock.tools) == {"cama_exec", "cama_read_file", "cama_write_file"}

    def test_cama_exec_destructive_and_open_world(self):
        mock = self._registered()
        ann = mock.annotations["cama_exec"]
        assert ann["destructiveHint"] is True, "cama_exec runs arbitrary shell, must be destructiveHint=True"
        assert ann["openWorldHint"] is True, "cama_exec can reach anywhere, must be openWorldHint=True"
        assert ann["readOnlyHint"] is False

    def test_cama_write_file_destructive(self):
        mock = self._registered()
        ann = mock.annotations["cama_write_file"]
        assert ann["destructiveHint"] is True, "cama_write_file mutates the filesystem, must be destructiveHint=True"
        assert ann["readOnlyHint"] is False

    def test_cama_read_file_open_world_but_not_destructive(self):
        mock = self._registered()
        ann = mock.annotations["cama_read_file"]
        assert ann["destructiveHint"] is False, "reads don't mutate"
        assert ann["openWorldHint"] is True, "cama_read_file can read anywhere, must be openWorldHint=True"
        assert ann["readOnlyHint"] is True


class TestWriteAllowlist:
    """cama_write_file must refuse writes outside the allowlist."""

    def test_write_inside_default_allowlist_succeeds(self, tmp_path, monkeypatch):
        # Override allowlist to point at tmp_path so the test doesn't
        # touch the real ~/.cama directory.
        monkeypatch.setenv("CAMA_BRIDGE_WRITE_ALLOWLIST", str(tmp_path))
        target = tmp_path / "subdir" / "hello.txt"
        result = asyncio.run(bridge.cama_write_file(str(target), "hello world"))
        assert "Written:" in result
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_write_outside_allowlist_refused(self, tmp_path, monkeypatch):
        # Allowlist is tmp_path/allowed, target is tmp_path/escape
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setenv("CAMA_BRIDGE_WRITE_ALLOWLIST", str(allowed))
        target = tmp_path / "escape" / "bad.txt"
        result = asyncio.run(bridge.cama_write_file(str(target), "should not land"))
        assert result.startswith("Refused:"), f"expected refusal, got: {result!r}"
        assert not target.exists(), "file should not be created when refused"

    def test_traversal_dotdot_refused(self, tmp_path, monkeypatch):
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        monkeypatch.setenv("CAMA_BRIDGE_WRITE_ALLOWLIST", str(allowed))
        # Path containing '..' should be refused even if it would resolve
        # inside the allowlist, defense in depth.
        traversal = f"{allowed}{os.sep}..{os.sep}escape.txt"
        result = asyncio.run(bridge.cama_write_file(traversal, "no"))
        assert "Refused" in result and ".." in result

    def test_env_var_override_changes_allowlist(self, tmp_path, monkeypatch):
        # Two allowlist roots, separated by os.pathsep
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        monkeypatch.setenv(
            "CAMA_BRIDGE_WRITE_ALLOWLIST",
            f"{root_a}{os.pathsep}{root_b}",
        )

        target_a = root_a / "ok.txt"
        target_b = root_b / "ok.txt"
        target_c = tmp_path / "c" / "nope.txt"

        assert "Written:" in asyncio.run(bridge.cama_write_file(str(target_a), "a"))
        assert "Written:" in asyncio.run(bridge.cama_write_file(str(target_b), "b"))
        refusal = asyncio.run(bridge.cama_write_file(str(target_c), "c"))
        assert refusal.startswith("Refused:")

    def test_default_allowlist_used_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("CAMA_BRIDGE_WRITE_ALLOWLIST", raising=False)
        roots = bridge._resolved_write_allowlist()
        # We can't assume these directories exist on the test machine, but the
        # resolution should yield the three expected user-relative roots.
        resolved_names = {str(r) for r in roots}
        expected = {
            str(Path(os.path.expanduser("~/.cama")).resolve(strict=False)),
            str(Path(os.path.expanduser("~/Desktop/cama")).resolve(strict=False)),
            str(Path(os.path.expanduser("~/Desktop/ProjectCompanion")).resolve(strict=False)),
        }
        assert resolved_names == expected

    def test_symlink_escape_refused(self, tmp_path, monkeypatch):
        """A symlink inside the allowlist pointing outside it must be refused.

        The sandbox resolves symlinks before the allowlist check.
        """
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        allowed.mkdir()
        outside.mkdir()
        monkeypatch.setenv("CAMA_BRIDGE_WRITE_ALLOWLIST", str(allowed))

        # Create symlink: allowed/escape -> outside. On Windows this requires
        # privileges that the test runner may not have; skip gracefully.
        link = allowed / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            import pytest

            pytest.skip("symlink creation not supported in this environment")

        target = link / "bad.txt"
        result = asyncio.run(bridge.cama_write_file(str(target), "should not land"))
        assert result.startswith("Refused:"), f"expected refusal, got: {result!r}"
        assert not (outside / "bad.txt").exists()
