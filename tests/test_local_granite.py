"""The on-machine Granite provider, with a stand-in for llama_cpp so the test
runs anywhere. On LinuxONE the real llama_cpp from IBM's image is used."""

import json
import sys
import time
import types

import pytest

from guardian import ai_features, llm


class FakeLlama:
    calls = []
    slow = False

    def __init__(self, model_path, **kw):
        self.model_path = model_path

    def create_chat_completion(self, messages, max_tokens, temperature, stream, response_format=None):
        FakeLlama.calls.append({"messages": messages, "response_format": response_format})
        answer = json.dumps({"reply": "नमस्ते जी, क्या कोई आपसे कॉल पर पैसे भेजने को कह रहा है?",
                             "risk": "unclear", "signals": [], "done": False}, ensure_ascii=False)
        for i in range(0, len(answer), 8):
            if FakeLlama.slow:
                time.sleep(0.05)
            yield {"choices": [{"delta": {"content": answer[i:i + 8]}}]}


@pytest.fixture()
def local_ai(tmp_path, monkeypatch):
    gguf = tmp_path / "granite-3.3-2b-instruct-Q4_K_M.gguf"
    gguf.write_bytes(b"GGUF")
    monkeypatch.setitem(sys.modules, "llama_cpp", types.SimpleNamespace(Llama=FakeLlama))
    monkeypatch.setenv("GUARDIAN_AI", "on")
    monkeypatch.setenv("GUARDIAN_LOCAL_GRANITE", "on")
    monkeypatch.setenv("GRANITE_GGUF", str(gguf))
    for k in ("WATSONX_APIKEY", "WATSONX_PROJECT_ID", "WATSONX_URL", "OLLAMA_URL"):
        monkeypatch.setenv(k, "")
    FakeLlama.calls, FakeLlama.slow = [], False
    return llm.AI()


def test_local_granite_is_found_and_used(local_ai):
    assert local_ai.status()["provider"] == "granite-local"
    t = {"id": "GP1", "amount": 50000, "payee": {"name": "X", "account": "1"},
         "sender": {"name": "S", "age": 70, "city": "Pune"}, "reasons": [], "features": {"new_payee": 1}}
    out = ai_features.saathi_turn(local_ai, t, None)
    assert out["provider"] == "granite-local" and "कॉल" in out["reply"]
    assert FakeLlama.calls[-1]["response_format"] == {"type": "json_object"}


def test_local_granite_respects_the_deadline(local_ai, monkeypatch):
    FakeLlama.slow = True
    local_ai.providers[0].deadline = 0.1
    with pytest.raises(llm.AIError):
        local_ai.chat([{"role": "user", "content": "hi"}])


def test_local_granite_refuses_images(local_ai):
    with pytest.raises(llm.AIError):
        local_ai.chat([{"role": "user", "content": "x"}], vision=True)
