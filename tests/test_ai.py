import json

import httpx
import pytest

from mazecrypto.providers.ai import AiUnavailable, LocalAI


def test_remote_hosts_are_refused():
    for host in ("http://example.com:11434", "https://127.0.0.1:11434", "http://10.0.0.5:11434"):
        with pytest.raises(AiUnavailable):
            LocalAI(host)


async def test_models_and_streaming():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "nomic-embed-text"}, {"name": "gemma4:e2b"}]})
        sent = json.loads(req.content)
        assert sent["messages"][0]["role"] == "system" and "DATA:" in sent["messages"][1]["content"]
        lines = [{"message": {"content": "Hello "}}, {"message": {"content": "there"}}, {"done": True}]
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))

    ai = LocalAI(transport=httpx.MockTransport(handler))
    assert await ai.models() == ["gemma4:e2b"]
    assert await ai.pick_model("gemma4") == "gemma4:e2b"
    text = "".join([c async for c in ai.ask("hi", {"x": 1}, "gemma4:e2b")])
    assert text == "Hello there"
    await ai.close()


async def test_unreachable_is_a_clean_error():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    ai = LocalAI(transport=httpx.MockTransport(handler))
    with pytest.raises(AiUnavailable):
        await ai.models()
    await ai.close()
