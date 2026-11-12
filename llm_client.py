# llm_client.py -- minimal OpenAI chat-completions client over httpx.
# Used when the `openai` SDK cannot be imported (e.g. its native `jiter` DLL is blocked by an
# OS application-control policy) or when LLM_TRANSPORT=httpx. Same endpoint, same payloads.
import os, time
from types import SimpleNamespace as NS
import httpx

class _Msg:
    def __init__(self, d: dict):
        self.content = d.get("content")
        tcs = d.get("tool_calls") or None
        self.tool_calls = [NS(id=t["id"], function=NS(name=t["function"]["name"],
                                                      arguments=t["function"].get("arguments") or ""))
                           for t in tcs] if tcs else None
        self._tcs = tcs
    def model_dump(self, exclude_none=True) -> dict:
        d = {"role": "assistant", "content": self.content}
        if self._tcs:
            d["tool_calls"] = [{"id": t["id"], "type": "function",
                                "function": {"name": t["function"]["name"],
                                             "arguments": t["function"].get("arguments") or ""}}
                               for t in self._tcs]
        return {k: v for k, v in d.items() if v is not None} if exclude_none else d

class HttpxOpenAI:
    kind = "httpx-rest"
    def __init__(self, api_key=None, base_url=None, timeout=120.0, transport=None, retries=3):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.base = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.retries = retries
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, model, messages, tools=None, **kw):
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        body = {"model": model, "messages": messages, **kw}
        if tools:
            body["tools"] = tools
        for attempt in range(self.retries + 1):       # safe to retry: an LLM call moves no money
            r = self._http.post(f"{self.base}/chat/completions", json=body,
                                headers={"Authorization": f"Bearer {self.api_key}"})
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                time.sleep(min(2 ** attempt, 8)); continue
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            return NS(choices=[NS(message=_Msg(msg))])

def make_client():
    """SDK if it imports, otherwise httpx. LLM_TRANSPORT=httpx forces httpx for the whole run."""
    if os.environ.get("LLM_TRANSPORT", "").lower() != "httpx":
        try:
            from openai import OpenAI
            c = OpenAI()
            c.kind = "openai-sdk"
            return c
        except ImportError:
            pass
    return HttpxOpenAI()