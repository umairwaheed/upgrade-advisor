"""Thin wrapper over the AgentCore Code Interpreter.

Everything that touches the network or does arithmetic runs *here*, inside the
managed sandbox — not in the model's head. The model never computes a version
ladder or a risk score; it reads the numbers this module returns.

The contract with sandboxed code is a JSON envelope on stdout, so a stray
print or a warning on stderr can't corrupt the payload.
"""

import json
import logging
import textwrap
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter, code_session

from .config import REGION

logger = logging.getLogger(__name__)

_BEGIN = "<<<ADVISOR_JSON"
_END = "ADVISOR_JSON>>>"


class SandboxError(RuntimeError):
    """Raised when sandboxed code fails or returns an unreadable payload."""


@contextmanager
def sandbox(region: str = REGION) -> Iterator[CodeInterpreter]:
    """Open a Code Interpreter session.

    One session per advisory run: variables and installed packages persist
    across `run_python` calls within the block, and the session is torn down
    on exit.
    """
    with code_session(region) as client:
        yield client


def _collect_text(response: Dict[str, Any]) -> str:
    """Flatten the event stream returned by invoke_code_interpreter."""
    chunks = []
    for event in response.get("stream", []):
        result = event.get("result") or {}
        if result.get("isError"):
            for item in result.get("content", []):
                if item.get("type") == "text":
                    chunks.append(item["text"])
            raise SandboxError("\n".join(chunks) or "sandbox reported an error")
        for item in result.get("content", []):
            if item.get("type") == "text":
                chunks.append(item["text"])
    return "\n".join(chunks)


def run_python(client: CodeInterpreter, code: str) -> str:
    """Execute Python in the sandbox and return combined stdout."""
    return _collect_text(client.execute_code(textwrap.dedent(code), language="python"))


def run_python_json(client: CodeInterpreter, code: str) -> Any:
    """Execute Python that emits a JSON envelope, and return the parsed value.

    Sandboxed code should call `emit(obj)` — injected by this helper — exactly
    once. Anything else it prints is treated as diagnostics and ignored.
    """
    preamble = f"""
        import json as _json
        def emit(_obj):
            print({_BEGIN!r} + _json.dumps(_obj, default=str) + {_END!r})
    """
    output = run_python(client, textwrap.dedent(preamble) + "\n" + textwrap.dedent(code))

    start = output.find(_BEGIN)
    end = output.find(_END, start + 1)
    if start == -1 or end == -1:
        raise SandboxError(
            "sandboxed code did not emit a JSON envelope. Raw output:\n" + output[:4000]
        )
    payload = output[start + len(_BEGIN) : end]
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SandboxError(f"malformed JSON envelope: {exc}") from exc


def read_file(client: CodeInterpreter, path: str) -> Optional[bytes]:
    """Best-effort read of a file the sandbox produced (e.g. a chart)."""
    try:
        content = client.download_file(path)
    except Exception as exc:  # noqa: BLE001 - artifacts are never load-bearing
        logger.warning("could not download %s: %s", path, exc)
        return None
    return content.encode("utf-8") if isinstance(content, str) else content
