# Scripted stand-in for the OpenAI client. Lets us test wiring without a key.
# It is NOT a model: it replays fixed tool calls. Results from it say nothing about model behavior.
import json
from types import SimpleNamespace as NS

def _call(i, name, args):
    return NS(id=f"c{i}", function=NS(name=name, arguments=json.dumps(args)))

class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls
    def model_dump(self, exclude_none=True):
        d = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name,
                                             "arguments": c.function.arguments}} for c in self.tool_calls]
        return d

class FakeClient:
    """steps: list of ('fetch_page'|'purchase', args). After the last one, replies 'done'."""
    def __init__(self, steps):
        self._steps, self._i = list(steps), 0
        self.chat = NS(completions=NS(create=self._create))
    def _create(self, model, messages, tools):
        if self._i < len(self._steps):
            name, args = self._steps[self._i]; self._i += 1
            msg = _Msg(tool_calls=[_call(self._i, name, args)])
        else:
            msg = _Msg(content="done")
        return NS(choices=[NS(message=msg)])

def buy(url, pid, qty, total):
    return ("purchase", {"merchant_url": url, "product_id": pid, "quantity": qty, "total_usd": total})
