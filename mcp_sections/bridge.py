"""Bridge tools: exec, read_file, write_file."""

import os


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
    Returns confirmation with file size."""
    try:
        path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        size = os.path.getsize(path)
        return f"Written: {path} ({size} bytes)"
    except Exception as e:
        return f"Error writing file: {str(e)}"


def register(mcp):
    """Attach this section's tools to the given FastMCP instance."""
    mcp.tool(
        name="cama_exec",
        annotations={"title":"Execute Command","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":True},
    )(cama_exec)
    mcp.tool(
        name="cama_read_file",
        annotations={"title":"Read File","readOnlyHint":True,"destructiveHint":False,"idempotentHint":True,"openWorldHint":False},
    )(cama_read_file)
    mcp.tool(
        name="cama_write_file",
        annotations={"title":"Write File","readOnlyHint":False,"destructiveHint":False,"idempotentHint":False,"openWorldHint":False},
    )(cama_write_file)
