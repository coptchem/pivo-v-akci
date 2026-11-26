import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeClient:
    """Napodobí anthropic klienta: vrací předem připravené odpovědi."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        stop = "end_turn"
        if isinstance(reply, tuple):
            stop, reply = reply
        text = json.dumps({"offers": reply}, ensure_ascii=False)
        return SimpleNamespace(
            stop_reason=stop,
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        )


@pytest.fixture
def fake_client():
    return FakeClient
