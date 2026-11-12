# Offline tests for the httpx fallback client (httpx.MockTransport; no network, no key).
import json
import httpx, pytest
import agent
from llm_client import HttpxOpenAI, make_client
from merchant.site import PRODUCTS

def client_for(handler, **kw):
    return HttpxOpenAI(api_key="test", transport=httpx.MockTransport(handler), **kw)

def tool_reply(i, name, args):
    return {"choices": [{"message": {"role": "assistant", "content": None, "refusal": None, "annotations": [],
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}}]}}]}

def test_parses_tool_calls_and_strips_extra_fields():
    c = client_for(lambda req: httpx.Response(200, json=tool_reply(1, "purchase", {"product_id": "paper"})))
    m = c.chat.completions.create(model="m", messages=[], tools=[]).choices[0].message
    assert m.tool_calls[0].function.name == "purchase"
    d = m.model_dump(exclude_none=True)
    assert set(d) == {"role", "tool_calls"} and d["tool_calls"][0]["type"] == "function"

def test_plain_text_reply():
    c = client_for(lambda req: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "hi"}}]}))
    m = c.chat.completions.create(model="m", messages=[]).choices[0].message
    assert m.content == "hi" and m.tool_calls is None

def test_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr("llm_client.time.sleep", lambda s: None)
    calls = []
    def h(req):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    assert client_for(h).chat.completions.create(model="m", messages=[]).choices[0].message.content == "ok"
    assert len(calls) == 3

def test_does_not_retry_client_errors():
    calls = []
    def h(req): calls.append(1); return httpx.Response(400, json={"error": "bad"})
    with pytest.raises(httpx.HTTPStatusError):
        client_for(h).chat.completions.create(model="m", messages=[])
    assert len(calls) == 1

def test_missing_key_is_clear_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        HttpxOpenAI(transport=httpx.MockTransport(lambda r: httpx.Response(200))).chat.completions.create(model="m", messages=[])

def test_forced_httpx_transport(monkeypatch):
    monkeypatch.setenv("LLM_TRANSPORT", "httpx"); monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert make_client().kind == "httpx-rest"

def test_agent_end_to_end_over_httpx_client(monkeypatch):
    monkeypatch.setattr(agent, "price_lookup", lambda pid: PRODUCTS[pid]["price"])
    script = [tool_reply(1, "purchase", {"merchant_url": "http://localhost:8001", "product_id": "chair",
                                         "quantity": 1, "total_usd": 140}),
              {"choices": [{"message": {"role": "assistant", "content": "done"}}]}]
    it = iter(script)
    c = client_for(lambda req: httpx.Response(200, json=next(it)))
    out = agent.run_agent("buy a chair", "protected", approver=lambda a: True, client=c)
    assert [e["state"] for e in out["log"]] == ["NEEDS_APPROVAL", "CAPTURE_SIMULATED"]
    assert out["client"] == "httpx-rest"