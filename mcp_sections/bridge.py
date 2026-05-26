"""Bridge tools: exec, read_file, write_file."""

import os
from pathlib import Path

DEFAULT_WRITE_ALLOWLIST = (
    "~/.cama",
    "~/Desktop/cama",
    "~/Desktop/ProjectCompanion",
)


def _resolved_write_allowlist() -> list[Path]:
    """Resolve the set of directory roots cama_write_file is allowed to write under.

    Read at call time (not import time) so tests and operators can override via the
    CAMA_BRIDGE_WRITE_ALLOWLIST env var without restarting the process. The env var
    is split on os.pathsep (``;`` on Windows, ``:`` on POSIX) so absolute paths on
    Windows, which contain ``:`` after the drive letter, survive the split.
    """
    override = os.environ.get("CAMA_BRIDGE_WRITE_ALLOWLIST")
    if override:
        entries = [e for e in override.split(os.pathsep) if e.strip()]
    else:
        entries = list(DEFAULT_WRITE_ALLOWLIST)
    resolved: list[Path] = []
    for entry in entries:
        try:
            resolved.append(Path(os.path.expanduser(entry)).resolve(strict=False))
        except (OSError, RuntimeError):
            continue
    return resolved


def _check_write_path(path: str) -> tuple[Path | None, str | None]:
    """Validate a cama_write_file target. Returns (resolved_path, None) on success
    or (None, error_message) on refusal.

    Refuses:
    - ``..`` traversal segments in the raw input
    - resolved target outside every allowlist root (catches symlink escapes)
    """
    if ".." in Path(path).parts:
        return None, f"Refused: path contains '..' traversal: {path}"

    expanded = os.path.expanduser(path)
    try:
        resolved = Path(expanded).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        return None, f"Refused: cannot resolve path: {exc}"

    allowlist = _resolved_write_allowlist()
    for root in allowlist:
        try:
            resolved.relative_to(root)
            return resolved, None
        except ValueError:
            continue

    roots_str = ", ".join(str(r) for r in allowlist) or "(empty allowlist)"
    return None, (
        f"Refused: write target outside allowlist. Resolved: {resolved}. "
        f"Allowed roots: {roots_str}. Override with CAMA_BRIDGE_WRITE_ALLOWLIST."
    )


async def cama_exec(command: str, timeout: int = 30) -> str:
    """Run a shell command on Angela's machine. Returns stdout, stderr, and return code.
    Default timeout is 30 seconds. Use for file operations, git, system checks, etc.

    FIXED 2026-04-02: Uses asyncio subprocess to avoid blocking the MCP event loop.
    Old version used subprocess.run (synchronous) which froze the server during long
    commands, causing Claude Desktop to think the server was dead."""
    import asyncio
    try:
        env = os.environ.copy()
        env["CAMA_DB_BUSY"] = "1"  # Signal child processes to use WAL + busy_timeout
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.expanduser("~"),
            env=env
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Command timed out after {timeout} seconds"
        stdout = (stdout_bytes.decode("utf-8", errors="replace"))[:10000] if stdout_bytes else ""
        stderr = (stderr_bytes.decode("utf-8", errors="replace"))[:5000] if stderr_bytes else ""
        output = f"Return code: {proc.returncode}"
        if stdout:
            output += f"\n\nSTDOUT:\n{stdout}"
            if stdout_bytes and len(stdout_bytes) > 10000:
                output += "\n... (truncated)"
        if stderr:
            output += f"\n\nSTDERR:\n{stderr}"
            if stderr_bytes and len(stderr_bytes) > 5000:
                output += "\n... (truncated)"
        return output
    except Exception as e:
        return f"Error executing command: {str(e)}"


async def cama_read_file(path: str, max_lines: int = 100) -> str:
    """Read a file from Angela's machine. Returns file content up to max_lines.
    Reports file size and whether content was truncated."""
    try:
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            return f"File not found: {path}"
        size = os.path.getsize(path)
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line)
        content = ''.join(lines)
        total_lines = sum(1 for _ in open(path, 'r', encoding='utf-8', errors='replace'))
        header = f"File: {path} | Size: {size} bytes | Lines: {total_lines}"
        if total_lines > max_lines:
            header += f" | Showing first {max_lines} lines"
        return f"{header}\n{'─'*60}\n{content}"
    except Exception as e:
        return f"Error reading file: {str(e)}"


async def cama_write_file(path: str, content: str) -> str:
    """Write content to a file on Angela's machine. Creates parent directories if needed.
    Returns confirmation with file size.

    Writes are restricted to an allowlist of roots (default: ~/.cama, ~/Desktop/cama,
    ~/Desktop/ProjectCompanion). Override via CAMA_BRIDGE_WRITE_ALLOWLIST. Paths with
    ``..`` segments are refused; symlinks are resolved before the allowlist check, so
    a symlink inside an allowed root that points elsewhere is also refused."""
    try:
        resolved, error = _check_write_path(path)
        if error is not None:
            return error
        assert resolved is not None  # narrow for type-checkers
        parent = resolved.parent
        parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, 'w', encoding='utf-8') as f:
            f.write(content)
        size = resolved.stat().st_size
        return f"Written: {resolved} ({size} bytes)"
    except Exception as e:
        return f"Error writing file: {str(e)}"


def register(mcp):
    """Attach this section's tools to the given FastMCP instance.

    Annotation honesty:
    - cama_exec: destructiveHint=True (arbitrary shell), openWorldHint=True
    - cama_write_file: destructiveHint=True (filesystem mutation, even sandboxed)
    - cama_read_file: destructiveHint=False but openWorldHint=True (read scope is unbounded)
    """
    mcp.tool(
        name="cama_exec",
        annotations={"title":"Execute Command","readOnlyHint":False,"destructiveHint":True,"idempotentHint":False,"openWorldHint":True},
    )(cama_exec)
    mcp.tool(
        name="cama_read_file",
        annotations={"title":"Read File","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":True},
    )(cama_read_file)
    mcp.tool(
        name="cama_write_file",
        annotations={"title":"Write File","readOnlyHint":False,"destructiveHint":True,"idempotentHint":False,"openWorldHint":False},
    )(cama_write_file)
