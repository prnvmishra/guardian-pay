import os

os.environ.setdefault("GUARDIAN_SIM", "0")

import pytest
from fastapi.testclient import TestClient

from guardian.engine import Engine
from guardian.features import CALM, FEATURES

engine = Engine()

CALM_BEHAVIOUR = dict(app_switches=0, pasted_account=False, confirm_hesitation_s=3,
                      amount_edits=0, retries_after_limit=0, session_duration_s=25)
SCARED_BEHAVIOUR = dict(app_switches=3, pasted_account=True, confirm_hesitation_s=25,
                        amount_edits=1, retries_after_limit=1, session_duration_s=180)


def coerced():
    f = dict(CALM)
    f.update(app_switches=3, pasted_account=1, retries_after_limit=1, amount_ratio=180,
             balance_drain=0.8, new_payee=1, payee_history=0, fd_broken_24h=1, sender_age=68)
    return f


def test_feature_lists_match():
    assert set(CALM) == set(FEATURES)


def test_regular_payment_takes_fast_lane():
    r = engine.decide(dict(CALM))
    assert r["decision"] == "allow" and r["path"] == "rules"


def test_coerced_payment_is_paused_with_reasons():
    r = engine.decide(coerced())
    assert r["decision"] == "pause"
    assert r["score"] >= 70
    assert r["reasons"], "a paused payment must say why"


def test_reported_account_is_blocked():
    r = engine.decide(dict(CALM), receiver_reported=True)
    assert r["decision"] == "block"


def test_community_flag_pauses_a_calm_payment():
    f = dict(CALM, new_payee=1, payee_history=0, amount_ratio=6)
    assert engine.decide(f)["decision"] == "allow"
    r = engine.decide(f, receiver_flag={"minutes_ago": 2})
    assert r["decision"] == "pause"
    assert any("Another customer" in x for x in r["rules"])


def test_missing_feature_is_rejected():
    f = dict(CALM)
    f.pop("app_switches")
    with pytest.raises(ValueError):
        engine.decide(f)


@pytest.fixture()
def client():
    from guardian.server import app, bank
    bank.reset()
    with TestClient(app) as c:
        yield c


def pay(c, account, amount, behaviour, name=None):
    return c.post("/api/pay", json={"payee_account": account, "payee_name": name,
                                    "amount": amount, "behaviour": behaviour}).json()


def test_everyday_payment_goes_through(client):
    r = pay(client, "40220018833012", 1500, CALM_BEHAVIOUR)
    assert r["txn"]["status"] == "paid"
    assert r["customer"]["balance"] == 240000 - 1500


def test_digital_arrest_flow_and_flag(client):
    r = pay(client, "50100447122906", 650000, SCARED_BEHAVIOUR, "R K Enterprises")
    assert r["error"] == "insufficient"
    assert client.post("/api/fd/break").json()["fd"] == 0

    t = pay(client, "50100447122906", 650000, SCARED_BEHAVIOUR, "R K Enterprises")["txn"]
    assert t["status"] == "held"

    t = client.post(f"/api/txn/{t['id']}/answers",
                    json={"on_call": True, "keep_secret": False, "threatened": True}).json()["txn"]
    assert t["status"] == "stopped"

    flagged = client.get("/api/stats")  # still serves after the flag
    assert flagged.status_code == 200

    # the next calm payment to the same account is caught by the flag
    t2 = pay(client, "50100447122906", 20000, CALM_BEHAVIOUR, "R K Enterprises")["txn"]
    assert t2["decision"] == "pause"
    assert t2["caught_by_flag"] is True


def test_guardian_can_release(client):
    client.post("/api/fd/break")
    t = pay(client, "50100447122906", 650000, SCARED_BEHAVIOUR, "R K Enterprises")["txn"]
    client.post(f"/api/txn/{t['id']}/answers", json={"on_call": False, "keep_secret": False, "threatened": False})
    t = client.post(f"/api/txn/{t['id']}/guardian", json={"approve": True}).json()["txn"]
    assert t["status"] == "paid"


def test_bad_input_is_rejected(client):
    r = client.post("/api/pay", json={"payee_account": "12", "amount": -5, "behaviour": CALM_BEHAVIOUR})
    assert r.status_code == 422
    assert client.get("/api/txn/GP0").status_code == 404
