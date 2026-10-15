"""Optional local AI: explains the user's data through Ollama on this machine.

Privacy: this client only ever talks to Ollama on the loopback interface — the
host is fixed, the proxy is never used, and a remote address is refused — so
the portfolio summary it sends never leaves the computer. If Ollama is not
installed or not running, the feature simply stays hidden.

The model defaults to the one chosen in Maze-AI (~/.config/maze-ai/config.json),
so the two apps share one download.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

import httpx

HOST = "http://127.0.0.1:11434"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

SYSTEM = """You are the assistant inside Maze Crypto, a read-only crypto portfolio tracker.
Answer in {language}. Use plain words a beginner understands; explain any term you use.
You receive the user's portfolio, plan and market data as JSON. Rules you must follow:
- Never tell the user to buy, sell or hold a specific asset, and never predict prices.
- You may explain the numbers, the user's own targets, plan and alerts, what indicators mean,
  common risks, and what questions the user could ask themselves.
- If asked for a recommendation, explain you cannot give investment advice and offer to explain
  the relevant numbers or concepts instead.
- Use only the data given; if something is missing, say so. Keep answers under 160 words.
- Plain text, no markdown tables, no emoji."""


class AiUnavailable(Exception):
    pass


def maze_ai_model() -> str | None:
    """The Ollama model selected in Maze-AI, if any."""
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    try:
        data = json.loads((base / "maze-ai" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    model = data.get("ollama_model") if isinstance(data, dict) else None
    return str(model) if model else None


def _check_host(host: str) -> str:
    u = urlparse(host)
    if u.scheme != "http" or u.hostname not in LOOPBACK:
        raise AiUnavailable("only a local Ollama is allowed")
    return host.rstrip("/")


class LocalAI:
    def __init__(self, host: str = HOST, transport: httpx.AsyncBaseTransport | None = None):
        self.host = _check_host(host)
        # trust_env=False: never route loopback traffic through HTTP(S)_PROXY variables.
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=2), trust_env=False,
                                         transport=transport)

    async def close(self) -> None:
        await self._client.aclose()

    async def models(self) -> list[str]:
        try:
            r = await self._client.get(f"{self.host}/api/tags")
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise AiUnavailable(str(e) or type(e).__name__) from e
        names = [str(m.get("name")) for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]
        # Embedding models cannot chat.
        return [n for n in names if "embed" not in n.lower()]

    async def pick_model(self, preferred: str = "") -> str:
        available = await self.models()
        if not available:
            raise AiUnavailable("no Ollama model installed")
        for want in (preferred, maze_ai_model()):
            if want:
                match = next((m for m in available if m == want or m.split(":")[0] == want.split(":")[0]), None)
                if match:
                    return match
        return available[0]

    async def ask(self, question: str, context: dict, model: str, language: str = "English") -> AsyncIterator[str]:
        """Stream the answer in small text chunks."""
        body = {
            "model": model,
            "stream": True,
            "options": {"temperature": 0.3},
            "messages": [
                {"role": "system", "content": SYSTEM.format(language=language)},
                {"role": "user", "content": "DATA:\n" + json.dumps(context, ensure_ascii=False)
                 + "\n\nQUESTION:\n" + question.strip()[:1000]},
            ],
        }
        try:
            async with self._client.stream("POST", f"{self.host}/api/chat", json=body) as r:
                if r.status_code != 200:
                    raise AiUnavailable(f"Ollama HTTP {r.status_code}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("error"):
                        raise AiUnavailable(str(msg["error"]))
                    chunk = (msg.get("message") or {}).get("content", "")
                    if chunk:
                        yield chunk
                    # No break on "done": Ollama closes the stream itself, and leaving it
                    # half-read would make httpx clean up after the event loop is gone.
        except httpx.HTTPError as e:
            raise AiUnavailable(str(e) or type(e).__name__) from e
