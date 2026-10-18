import os

os.environ.setdefault("GUARDIAN_SIM", "0")

import pytest
from fastapi.testclient import TestClient

from guardian import ai_features, llm
from tests.fake_watsonx import start

PNG_1PX = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
           "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.fixture()
def fake(monkeypatch):
    server, state = start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("GUARDIAN_AI", "on")
    monkeypatch.setenv("WATSONX_APIKEY", "test-key")
    monkeypatch.setenv("WATSONX_PROJECT_ID", "proj-1")
    monkeypatch.setenv("WATSONX_URL", base)
    monkeypatch.setenv("WATSONX_IAM_URL", base + "/identity/token")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.delenv("WATSONX_CHAT_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    yield llm.AI(), state
    server.shutdown()


@pytest.fixture()
def offline(monkeypatch):
    for k in ("WATSONX_APIKEY", "WATSONX_PROJECT_ID", "WATSONX_URL", "OLLAMA_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GUARDIAN_AI", "off")
    return llm.AI()


def held_txn():
    return {"id": "GP1", "amount": 650000, "payee": {"name": "R K Enterprises", "account": "50100447122906"},
            "sender": {"name": "Suresh Sharma", "age": 66, "city": "Lucknow"}, "score": 98,
            "decision": "pause", "status": "held", "rules": [],
            "reasons": [{"en": "Empties 77% of available balance", "hi": "", "weight": 2}],
            "features": {"new_payee": 1, "app_switches": 2}}


# ------------------------------------------------------------- client
def test_discovers_granite_and_vision_model(fake):
    ai, state = fake
    ai.providers[0].discover()
    assert ai.providers[0].chat_model == "ibm/granite-3-3-8b-instruct"
    assert ai.providers[0].vision_model == "meta-llama/llama-3-2-11b-vision-instruct"


def test_token_is_cached(fake):
    ai, state = fake
    ai.chat([{"role": "user", "content": "hi"}])
    ai.chat([{"role": "user", "content": "hi again"}])
    assert sum(1 for r in state.requests if r[1] == "/identity/token") == 1


def test_parse_json_handles_fences_and_noise():
    assert llm.parse_json('Here:\n```json\n{"a": "x}y", "b": 2}\n```') == {"a": "x}y", "b": 2}
    assert llm.parse_json("no json here") is None
    assert llm.parse_json('{"a": 1') is None


def test_bad_json_is_repaired_once(fake):
    ai, state = fake
    state.mode = "garbage_once"
    out = ai_features.saathi_turn(ai, held_txn(), None)
    assert out["provider"] == "watsonx"
    assert out["reply"]


def test_wrong_api_key_falls_back(fake, monkeypatch):
    monkeypatch.setenv("WATSONX_APIKEY", "wrong")
    ai = llm.AI()
    out = ai_features.saathi_turn(ai, held_txn(), None)
    assert out["provider"] == "safety-net"
    assert out["reply"] == ai_features.SAATHI_SCRIPT[0]
    assert "could not be found" in ai.status()["error"]


# ------------------------------------------------------------- Saathi
def test_saathi_calm_conversation(fake):
    ai, _ = fake
    t = held_txn()
    first = ai_features.saathi_turn(ai, t, None)
    assert first["provider"] == "watsonx" and not first["done"]
    out = ai_features.saathi_turn(ai, t, "nahi, meri beti ki fees hai")
    assert out["risk"] == "low" and not out["done"]
    out = ai_features.saathi_turn(ai, t, "nahi koi call pe nahi hai, beti ko main khud bhej raha hoon")
    assert out["risk"] == "low" and out["done"]
    assert out["reply"] == ai_features.SAATHI_CALM_END


def test_safety_net_overrides_a_calm_model(fake):
    ai, state = fake
    t = held_txn()
    ai_features.saathi_turn(ai, t, None)
    out = ai_features.saathi_turn(ai, t, "haan CBI officer video call pe hai, bole kisi ko mat batana")
    assert out["risk"] == "high" and out["done"]
    assert out["reply"] == ai_features.SAATHI_SAFE_WARNING
    assert "Was told to keep it secret" in out["signals"]


def test_saathi_when_service_is_down(fake):
    ai, state = fake
    state.mode = "down"
    t = held_txn()
    assert ai_features.saathi_turn(ai, t, None)["provider"] == "safety-net"
    out = ai_features.saathi_turn(ai, t, "haan")
    assert out["risk"] == "high" and out["done"]


def test_saathi_offline_full_script(offline):
    t = held_txn()
    ai_features.saathi_turn(offline, t, None)
    for answer in ("nahi", "nahi ji", "नहीं"):
        out = ai_features.saathi_turn(offline, t, answer)
    assert out["done"] and out["risk"] == "low"


# ------------------------------------------------------------- message checker
def test_check_text_with_ai(fake):
    ai, _ = fake
    out = ai_features.check_message(ai, "This is CBI. Transfer to 5010 0447 1229 06 for verification.")
    assert out["verdict"] == "scam" and out["provider"] == "watsonx"
    assert "50100447122906" in out["accounts_found"]


def test_check_screenshot_uses_vision_model(fake):
    ai, state = fake
    out = ai_features.check_message(ai, None, PNG_1PX)
    assert out["verdict"] == "scam"
    chat = [r for r in state.requests if r[1] == "/ml/v1/text/chat"][-1]
    assert chat[3]["model_id"] == "meta-llama/llama-3-2-11b-vision-instruct"
    assert chat[3]["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/png")


def test_model_cannot_clear_an_obvious_scam(fake):
    ai, _ = fake
    out = ai_features.check_message(ai, "Police: aapke parcel mein drugs hai, kisi ko mat batana, abhi bhejo")
    assert out["verdict"] in ("scam", "suspicious")


def test_check_offline(offline):
    out = ai_features.check_message(offline, "Main CBI se bol raha hoon, digital arrest hai")
    assert out["verdict"] == "scam" and out["provider"] == "safety-net"
    assert ai_features.check_message(offline, "Rent for October, thanks")["verdict"] == "looks_safe"
    with pytest.raises(llm.AIError):
        ai_features.check_message(offline, None, PNG_1PX)


# ------------------------------------------------------------- summary
def test_summary_with_ai_and_offline(fake, offline):
    ai, _ = fake
    out = ai_features.case_summary(ai, held_txn())
    assert out["provider"] == "watsonx" and out["likely_scam_type"] == "digital_arrest"
    out = ai_features.case_summary(offline, held_txn())
    assert out["provider"] == "template" and "650,000" in out["summary"]


# ------------------------------------------------------------- through the API
@pytest.fixture()
def client(fake, monkeypatch):
    from guardian import server
    monkeypatch.setattr(server, "ai", fake[0])
    server.bank.reset()
    with TestClient(server.app) as c:
        yield c


SCARED = dict(app_switches=3, pasted_account=True, confirm_hesitation_s=25,
              amount_edits=1, retries_after_limit=1, session_duration_s=180)
CALM = dict(app_switches=0, pasted_account=False, confirm_hesitation_s=3,
            amount_edits=0, retries_after_limit=0, session_duration_s=25)


def test_api_saathi_flow_flags_account(client):
    client.post("/api/fd/break")
    t = client.post("/api/pay", json={"payee_account": "50100447122906", "payee_name": "R K Enterprises",
                                      "amount": 650000, "behaviour": SCARED}).json()["txn"]
    assert t["status"] == "held"
    r = client.post(f"/api/txn/{t['id']}/saathi", json={}).json()
    assert r["result"]["reply"]
    r = client.post(f"/api/txn/{t['id']}/saathi", json={"text": "haan police wale video call pe hain"}).json()
    assert r["result"]["risk"] == "high"
    r = client.post(f"/api/txn/{t['id']}/saathi/decide", json={"action": "cancel"}).json()
    assert r["txn"]["status"] == "stopped"
    t2 = client.post("/api/pay", json={"payee_account": "50100447122906", "amount": 15000,
                                       "behaviour": CALM}).json()["txn"]
    assert t2["caught_by_flag"]


def test_api_message_check_feeds_the_engine(client):
    c = client.post("/api/ai/check", json={"text": "CBI notice. Pay 5010 0447 1229 06 now."}).json()
    assert c["verdict"] == "scam"
    t = client.post("/api/pay", json={"payee_account": "50100447122906", "amount": 15000,
                                      "behaviour": CALM, "message_check_id": c["id"]}).json()["txn"]
    assert t["decision"] == "pause"
    assert any("scam script" in r for r in t["rules"])


def test_api_rejects_bad_ai_input(client):
    assert client.post("/api/ai/check", json={}).status_code == 422
    assert client.post("/api/ai/check", json={"image": "data:text/html;base64,AAAA"}).status_code == 422
    t = client.post("/api/pay", json={"payee_account": "40220018833012", "amount": 500,
                                      "behaviour": CALM}).json()["txn"]
    assert client.post(f"/api/txn/{t['id']}/saathi", json={"text": "hi"}).status_code == 409
