from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from tom.config import parse_interval_seconds

_log = logging.getLogger("tom")

_STDERR_TAIL_LINES = 20


@dataclass
class AgentSuccess:
    output: dict


@dataclass
class AgentFailure:
    reason: str
    exit_code: int | None = None
    stderr_tail: str | None = None


AgentResult = AgentSuccess | AgentFailure


async def spawn_agent(
    prompt: str,
    schema: dict,
    *,
    cwd: str,
) -> asyncio.subprocess.Process:
    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--permission-mode", "bypassPermissions",
    ]
    return await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def await_agent(
    proc: asyncio.subprocess.Process,
    *,
    timeout_str: str,
) -> AgentResult:
    timeout_seconds = parse_interval_seconds(timeout_str)

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        _log.warning("Agent timed out after %s, terminating", timeout_str)
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return AgentFailure(
            reason=f"Agent timed out after {timeout_str}",
            exit_code=None,
            stderr_tail=None,
        )

    exit_code = proc.returncode
    stderr_text = stderr_bytes.decode(errors="replace")
    stderr_tail = "\n".join(stderr_text.strip().splitlines()[-_STDERR_TAIL_LINES:])

    if exit_code != 0:
        _log.error("Agent exited %d, stderr: %s", exit_code, stderr_tail)
        return AgentFailure(
            reason=f"Agent exited with code {exit_code}",
            exit_code=exit_code,
            stderr_tail=stderr_tail,
        )

    stdout_text = stdout_bytes.decode(errors="replace")
    return _parse_output(stdout_text, stderr_tail)


async def run_agent(
    prompt: str,
    schema: dict,
    *,
    cwd: str,
    timeout_str: str,
) -> AgentResult:
    try:
        proc = await spawn_agent(prompt, schema, cwd=cwd)
    except FileNotFoundError:
        return AgentFailure(reason="claude CLI not found in PATH", exit_code=None)
    return await await_agent(proc, timeout_str=timeout_str)


def _parse_output(stdout: str, stderr_tail: str) -> AgentResult:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        _log.error("Agent returned non-JSON output")
        return AgentFailure(
            reason="Agent returned non-JSON output",
            exit_code=0,
            stderr_tail=stderr_tail,
        )

    if isinstance(data, dict) and "structured_output" in data:
        structured = data["structured_output"]
    elif isinstance(data, dict) and "result" in data:
        structured = data["result"]
    elif isinstance(data, list):
        for item in reversed(data):
            if isinstance(item, dict) and item.get("type") == "result":
                structured = item.get("result")
                if isinstance(structured, str):
                    try:
                        structured = json.loads(structured)
                    except json.JSONDecodeError:
                        pass
                break
        else:
            _log.error("No result message in agent output array")
            return AgentFailure(
                reason="No result message found in agent output",
                exit_code=0,
                stderr_tail=stderr_tail,
            )
    else:
        structured = data

    if not isinstance(structured, dict):
        _log.error("Structured output is not a dict: %s", type(structured))
        return AgentFailure(
            reason=f"Structured output is not a dict: {type(structured).__name__}",
            exit_code=0,
            stderr_tail=stderr_tail,
        )

    return AgentSuccess(output=structured)
