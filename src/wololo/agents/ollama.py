"""OllamaClient — a local language model at the keyboard.

Game analogy: the same human-like player as ``AnthropicClient``, except the
brain lives on a home-lab box instead of a cloud API.  CS meaning: an
``LlmClient`` *and* ``ToolLlmClient`` implementation over the Ollama HTTP
chat API, stdlib-only (``urllib``), so it adds no dependencies.  Reasoning
models (qwen3, gpt-oss) return their chain-of-thought in a separate
``thinking`` field, which we discard; only ``message.content`` is parsed.

Tool mode translates between the Anthropic-shaped conversation that
``ToolLlmAgent`` maintains (``tool_use`` / ``tool_result`` content blocks)
and Ollama's function-calling wire format (``tool_calls`` on the assistant
message, ``role: "tool"`` result messages).
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from typing import Any

from wololo.agents.llm import LlmReply, ToolCall

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "gpt-oss:20b"
#: Local models on modest hardware can take a while per turn.
DEFAULT_TIMEOUT = 300.0

#: POSTs a JSON payload to a URL, returns the decoded JSON response.
Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


def _normalize(base_url: str) -> str:
    """Accept the same shorthand as $OLLAMA_HOST: bare host, host:port, URL."""
    if "://" not in base_url:
        base_url = "http://" + base_url
    base_url = base_url.rstrip("/")
    scheme, _, rest = base_url.partition("://")
    if ":" not in rest:
        base_url = f"{scheme}://{rest}:11434"
    return base_url


class OllamaError(RuntimeError):
    """The Ollama server refused, timed out, or returned garbage."""


class OllamaClient:
    """Chat-completion client for an Ollama server (implements LlmClient)."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Transport | None = None,
    ) -> None:
        self._model = model
        self._url = _normalize(base_url) + "/api/chat"
        self._timeout = timeout
        self._transport = transport if transport is not None else self._http_post
        self._call_seq = 0
        self._call_names: dict[str, str] = {}

    def complete(self, *, system: str, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
        }
        data = self._transport(self._url, payload)
        message = data.get("message")
        if not isinstance(message, dict) or "content" not in message:
            raise OllamaError(f"malformed Ollama response: {data!r}")
        return message["content"]

    def complete_tools(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LlmReply:
        """Tool-mode turn (implements ToolLlmClient) via Ollama function calling."""
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *self._to_ollama(messages)],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ],
            "stream": False,
        }
        data = self._transport(self._url, payload)
        message = data.get("message")
        if not isinstance(message, dict):
            raise OllamaError(f"malformed Ollama response: {data!r}")
        text = message.get("content") or ""
        raw: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        calls: list[ToolCall] = []
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function", {})
            name = function.get("name", "")
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):  # some models return JSON-encoded args
                arguments = json.loads(arguments)
            self._call_seq += 1
            call_id = tool_call.get("id") or f"ollama_call_{self._call_seq}"
            self._call_names[call_id] = name
            calls.append(ToolCall(id=call_id, name=name, input=dict(arguments)))
            raw.append({"type": "tool_use", "id": call_id, "name": name, "input": dict(arguments)})
        if not raw:  # empty reply: keep the history block well-formed
            raw = [{"type": "text", "text": ""}]
        return LlmReply(raw_content=raw, tool_calls=tuple(calls), text=text)

    def _to_ollama(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Anthropic-shaped history (tool_use/tool_result blocks) → Ollama chat."""
        out: list[dict[str, Any]] = []
        for message in messages:
            content = message["content"]
            if isinstance(content, str):
                out.append({"role": message["role"], "content": content})
                continue
            if message["role"] == "assistant":
                text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
                tool_calls = [
                    {
                        "function": {
                            "name": block["name"],
                            "arguments": dict(block.get("input", {})),
                        }
                    }
                    for block in content
                    if block.get("type") == "tool_use"
                ]
                entry: dict[str, Any] = {"role": "assistant", "content": text}
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                out.append(entry)
            else:  # user turn carrying tool_result blocks
                for block in content:
                    if block.get("type") != "tool_result":
                        continue
                    name = self._call_names.get(block.get("tool_use_id", ""), "")
                    out.append({"role": "tool", "tool_name": name, "content": block["content"]})
        return out

    def _http_post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read())
        except OSError as exc:
            raise OllamaError(f"Ollama request to {url} failed: {exc}") from exc
