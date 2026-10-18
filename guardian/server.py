"""HTTP API, live event stream and the three web screens.

    uvicorn guardian.server:app --host 0.0.0.0 --port 8000

/            bank risk console
/pay         customer payment app (open on a phone)
/guardian    trusted-contact screen (open on a second phone)
"""

import asyncio
import itertools
import json
import os
import platform
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai_features
from .bank import COOLING_SECONDS, bank, ist_hour, mask, now
from .engine import Engine
from .llm import AI, AIError
from .text_model import load_default as load_text_model
from .simulator import Simulator

WEB = Path(__file__).resolve().parent.parent / "web"
SIM_INTERVAL = float(os.environ.get("GUARDIAN_SIM_INTERVAL", "1.2"))

engine = Engine()
sim = Simulator(bank)
ai = AI()
text_model = load_text_model()
check_ids = itertools.count(1001)
checks = {}          # message checks by id, kept for the payment that follows
background = set()   # keeps fire-and-forget tasks alive until they finish


def later(coro):
    task = asyncio.create_task(coro)
    background.add(task)
    task.add_done_callback(background.discard)


@asynccontextmanager
async def lifespan(_app):
    tasks = [asyncio.create_task(sim_loop()), asyncio.create_task(release_loop())]
    if ai.enabled and hasattr(ai.providers[0], "discover"):
        later(asyncio.to_thread(ai.providers[0].discover))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Guardian Pay", version="1.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

state = {"sim_on": os.environ.get("GUARDIAN_SIM", "1") != "0"}
listeners: set = set()


# ---------------------------------------------------------------- events
def publish(kind, txn=None, extra=None):
    msg = {"type": kind, "stats": bank.stats(), "flagged": bank.flagged()}
    if txn is not None:
        msg["txn"] = txn
    if extra:
        msg.update(extra)
    data = f"data: {json.dumps(msg)}\n\n"
    for q in list(listeners):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass


@app.get("/api/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    listeners.add(q)
    first = {"type": "snapshot", "txns": bank.recent(60), "stats": bank.stats(),
             "sim_on": state["sim_on"], "customer": public_customer(), "flagged": bank.flagged(),
             "model": engine.model.metrics}

    async def gen():
        try:
            yield f"data: {json.dumps(first)}\n\n"
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            listeners.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- helpers
def public_customer():
    c = bank.customer
    return {
        "name": c["name"], "age": c["age"], "city": c["city"],
        "balance": c["balance"], "fd": c["fd"], "guardian": c["guardian"],
        "payees": [{"account": a, "masked": mask(a), **p} for a, p in bank.payees.items()],
    }


def find(txn_id):
    t = bank.get(txn_id)
    if not t:
        raise HTTPException(404, "No payment with that id")
    return t


def decide_with_flag(feats, receiver, message_verdict=None):
    """Score once with the community flag, and once without it, so the
    console can show payments that only the flag caught."""
    reported = bool(receiver and receiver.reported)
    flag = receiver.flag_info() if receiver else None
    verdict = engine.decide(feats, receiver_reported=reported, receiver_flag=flag,
                            message_verdict=message_verdict)
    verdict["caught_by_flag"] = False
    if flag and verdict["decision"] in ("pause", "block"):
        alone = engine.decide(feats, receiver_reported=reported, message_verdict=message_verdict)
        verdict["caught_by_flag"] = alone["decision"] in ("allow", "caution")
        verdict["score_without_flag"] = alone["score"]
    return verdict


def note_saved(receiver, txn):
    if receiver is not None and receiver.flag and txn["decision"] in ("pause", "block"):
        receiver.saved_after_flag["count"] += 1
        receiver.saved_after_flag["amount"] += txn["amount"]


def build_txn(source, sender, payee, amount, verdict, feats):
    return {
        "id": bank.next_id(),
        "ts": now(),
        "source": source,
        "sender": sender,
        "payee": {**payee, "masked": mask(payee["account"])},
        "amount": amount,
        **verdict,
        "features": feats,
        "status": "",
    }


# ---------------------------------------------------------------- customer API
class Behaviour(BaseModel):
    app_switches: int = Field(0, ge=0, le=200)
    pasted_account: bool = False
    confirm_hesitation_s: float = Field(3, ge=0, le=3600)
    amount_edits: int = Field(0, ge=0, le=200)
    retries_after_limit: int = Field(0, ge=0, le=200)
    session_duration_s: float = Field(30, ge=0, le=7200)


class PayIn(BaseModel):
    payee_account: str = Field(min_length=6, max_length=24)
    payee_name: Optional[str] = Field(None, max_length=60)
    amount: int = Field(gt=0, le=1_000_000)
    behaviour: Behaviour
    message_check_id: Optional[str] = Field(None, max_length=40)


@app.get("/api/me")
async def me():
    return public_customer()


@app.post("/api/fd/break")
async def break_fd():
    if not bank.break_fd():
        raise HTTPException(409, "Your fixed deposit is already closed")
    publish("customer", extra={"customer": public_customer()})
    return public_customer()


@app.post("/api/pay")
async def pay(body: PayIn):
    acct = "".join(ch for ch in body.payee_account if ch.isalnum())
    if len(acct) < 6:
        raise HTTPException(422, "Enter a valid account number")
    c = bank.customer
    if body.amount > c["balance"]:
        # a normal answer, not an error: the app offers to break the FD
        return JSONResponse(content={
            "error": "insufficient",
            "message": "Not enough balance in savings",
            "balance": c["balance"], "fd": c["fd"],
        })

    saved = bank.payees.get(acct)
    receiver = bank.receiver(acct, body.payee_name)
    b = body.behaviour
    feats = {
        "app_switches": b.app_switches,
        "pasted_account": int(b.pasted_account),
        "confirm_hesitation_s": round(b.confirm_hesitation_s, 1),
        "amount_edits": b.amount_edits,
        "retries_after_limit": b.retries_after_limit,
        "session_duration_s": round(b.session_duration_s, 1),
        "amount_ratio": round(body.amount / c["avg_payment"], 2),
        "balance_drain": round(min(body.amount / max(c["balance"], 1), 1.0), 3),
        "new_payee": 0 if saved else 1,
        "payee_history": saved["history"] if saved else 0,
        "fd_broken_24h": bank.fd_broken_recently(),
        "night_hour": 1 if ist_hour(now()) >= 22 or ist_hour(now()) < 6 else 0,
        "sender_age": c["age"],
        **receiver.features(),
    }
    check = checks.get(body.message_check_id) if body.message_check_id else None
    msg_verdict = None
    if check and (not check["accounts_found"] or acct in check["accounts_found"]):
        msg_verdict = check["verdict"]
    verdict = decide_with_flag(feats, receiver, msg_verdict)
    pattern = engine.paysim_score(body.amount, c["balance"], receiver.balance_today(), ist_hour(now()))
    if pattern is not None:
        verdict["paysim"] = {"score": pattern}
        if pattern >= 80 and verdict["decision"] == "allow":
            verdict["decision"] = "caution"
            verdict["score"] = max(verdict["score"], 45)
            verdict["rules"].append("Matches fraud patterns learned from the PaySim dataset")
    name = saved["name"] if saved else (body.payee_name or receiver.name)
    sender = {"id": c["id"], "name": c["name"], "age": c["age"], "city": c["city"]}
    txn = build_txn("live", sender, {"name": name, "account": acct}, body.amount, verdict, feats)

    d = verdict["decision"]
    if d == "allow":
        bank.settle(txn)
    elif d == "caution":
        txn["status"] = "warned"
    elif d == "pause":
        txn["status"] = "held"
        txn["hold"] = {"until": now() + COOLING_SECONDS, "seconds": COOLING_SECONDS,
                       "answers": None, "guardian": None}
    else:
        txn["status"] = "blocked"
    if check:
        txn["message_check"] = {k: check[k] for k in ("verdict", "scam_type", "red_flags", "provider")}
    note_saved(receiver, txn)
    bank.add(txn)
    publish("txn", txn)
    if d == "pause":
        later(write_summary(txn))
    return {"txn": txn, "customer": public_customer()}


@app.get("/api/txn/{txn_id}")
async def get_txn(txn_id: str):
    return find(txn_id)


@app.post("/api/txn/{txn_id}/proceed")
async def proceed(txn_id: str):
    t = find(txn_id)
    if t["status"] != "warned":
        raise HTTPException(409, "This payment is not waiting for you to confirm")
    bank.settle(t)
    t["status_note"] = "Paid after the warning"
    publish("update", t)
    return {"txn": t, "customer": public_customer()}


@app.post("/api/txn/{txn_id}/cancel")
async def cancel(txn_id: str):
    t = find(txn_id)
    if t["status"] in ("paid", "stopped", "cancelled", "blocked"):
        raise HTTPException(409, "This payment is already closed")
    t["status"] = "cancelled"
    t["status_note"] = "Cancelled by the customer"
    publish("update", t)
    return {"txn": t, "customer": public_customer()}


class Answers(BaseModel):
    on_call: bool
    keep_secret: bool
    threatened: bool


@app.post("/api/txn/{txn_id}/answers")
async def answers(txn_id: str, body: Answers):
    t = find(txn_id)
    if t["status"] != "held":
        raise HTTPException(409, "This payment is not on hold")
    yes = [k for k, v in body.model_dump().items() if v]
    t["hold"]["answers"] = body.model_dump()
    if yes:
        t["status"] = "stopped"
        t["status_note"] = "Customer confirmed scam signs during the check"
        said = {"on_call": "someone on a call told them to pay",
                "keep_secret": "they were told to keep it secret",
                "threatened": "the caller claimed to be police or threatened arrest"}
        c = bank.customer
        bank.receiver(t["payee"]["account"]).raise_flag(
            f"{c['name']}, {c['age']}, {c['city']}", "Customer said " + "; ".join(said[k] for k in yes))
    else:
        t["status"] = "awaiting_guardian"
        t["status_note"] = f"Waiting for {bank.customer['guardian']['name']}"
    publish("update", t)
    return {"txn": t, "customer": public_customer()}


class GuardianIn(BaseModel):
    approve: bool


@app.post("/api/txn/{txn_id}/guardian")
async def guardian(txn_id: str, body: GuardianIn):
    t = find(txn_id)
    if t["status"] not in ("held", "awaiting_guardian"):
        raise HTTPException(409, "This payment no longer needs a decision")
    t["hold"]["guardian"] = "approved" if body.approve else "rejected"
    if body.approve:
        bank.settle(t)
        t["status_note"] = f"Approved by {bank.customer['guardian']['name']}"
    else:
        t["status"] = "stopped"
        t["status_note"] = f"Stopped by {bank.customer['guardian']['name']}"
        g = bank.customer["guardian"]
        bank.receiver(t["payee"]["account"]).raise_flag(
            f"{g['name']} ({g['relation'].lower()} of {bank.customer['name']})", "Family contact stopped the payment")
    publish("update", t)
    return {"txn": t, "customer": public_customer()}


# ---------------------------------------------------------------- generative AI
async def write_summary(txn):
    """Case note for the analyst and the family contact. Never blocks a reply."""
    try:
        txn["ai_summary"] = await asyncio.to_thread(ai_features.case_summary, ai, txn)
        publish("update", txn)
    except Exception as e:  # a summary must never take the payment flow down
        txn["ai_summary"] = {"summary": "", "error": str(e)[:200], "provider": "error"}


@app.get("/api/ai/status")
async def ai_status():
    return ai.status()


class SaathiIn(BaseModel):
    text: Optional[str] = Field(None, max_length=600)


@app.post("/api/txn/{txn_id}/saathi")
async def saathi(txn_id: str, body: SaathiIn):
    t = find(txn_id)
    if t["status"] != "held":
        raise HTTPException(409, "This payment is no longer on hold")
    if body.text is not None and not body.text.strip():
        raise HTTPException(422, "Say or type something first")
    turns = sum(1 for x in (t.get("saathi") or {}).get("turns", []) if x["role"] == "user")
    if turns >= 8:
        raise HTTPException(409, "The check is complete. Choose what to do with the payment.")
    result = await asyncio.to_thread(ai_features.saathi_turn, ai, t, body.text, text_model)
    publish("update", t)
    return {"result": result, "txn": t}


class SaathiDecision(BaseModel):
    action: str = Field(pattern="^(cancel|ask_guardian)$")


@app.post("/api/txn/{txn_id}/saathi/decide")
async def saathi_decide(txn_id: str, body: SaathiDecision):
    t = find(txn_id)
    if t["status"] != "held":
        raise HTTPException(409, "This payment is no longer on hold")
    s = t.get("saathi") or {}
    if body.action == "cancel":
        t["status"] = "stopped"
        t["status_note"] = "Customer stopped it after talking to Saathi"
        if s.get("risk") == "high":
            c = bank.customer
            bank.receiver(t["payee"]["account"]).raise_flag(
                f"{c['name']}, {c['age']}, {c['city']}",
                "Customer confirmed to Saathi: " + "; ".join(s.get("signals", [])[:3]).lower())
    else:
        t["hold"]["answers"] = {"saathi_risk": s.get("risk", "unclear")}
        t["status"] = "awaiting_guardian"
        t["status_note"] = f"Waiting for {bank.customer['guardian']['name']}"
    publish("update", t)
    later(write_summary(t))
    return {"txn": t, "customer": public_customer()}


class CheckIn(BaseModel):
    text: Optional[str] = Field(None, max_length=4000)
    image: Optional[str] = Field(None, max_length=6_000_000)


@app.post("/api/ai/check")
async def check(body: CheckIn):
    text = (body.text or "").strip()
    image = body.image
    if image and not image.startswith(("data:image/png;base64,", "data:image/jpeg;base64,",
                                       "data:image/webp;base64,")):
        raise HTTPException(422, "Upload a PNG, JPG or WebP screenshot")
    if not text and not image:
        raise HTTPException(422, "Paste the message or add a screenshot")
    try:
        result = await asyncio.to_thread(ai_features.check_message, ai, text, image, text_model)
    except AIError as e:
        raise HTTPException(503, str(e))
    check_id = f"MC{next(check_ids)}"
    result["id"] = check_id
    checks[check_id] = result
    while len(checks) > 500:
        checks.pop(next(iter(checks)))
    return result


@app.post("/api/ai/summary/{txn_id}")
async def summary(txn_id: str):
    t = find(txn_id)
    t["ai_summary"] = await asyncio.to_thread(ai_features.case_summary, ai, t)
    publish("update", t)
    return t["ai_summary"]


# ---------------------------------------------------------------- console API
class SimIn(BaseModel):
    on: bool


@app.post("/api/sim")
async def toggle_sim(body: SimIn):
    state["sim_on"] = body.on
    publish("sim", extra={"sim_on": body.on})
    return {"sim_on": body.on}


@app.post("/api/reset")
async def reset():
    bank.reset()
    checks.clear()
    publish("reset", extra={"txns": [], "customer": public_customer()})
    return {"ok": True}


@app.get("/api/health")
async def health():
    m = engine.model.metrics
    return {
        "ok": True,
        "ai": ai.status(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "trees": len(engine.model.trees),
        "paysim_model": engine.paysim.metrics if engine.paysim else None,
        "text_model": text_model.metrics if text_model else None,
        "model": {k: m.get(k) for k in ("rows", "auc", "avg_precision", "at_70")},
    }


@app.get("/api/stats")
async def stats():
    return bank.stats()


# ---------------------------------------------------------------- background
async def sim_loop():
    while True:
        await asyncio.sleep(SIM_INTERVAL)
        if not state["sim_on"]:
            continue
        persona, f, sender, payee, amount, receiver = sim.next()
        verdict = decide_with_flag(f, receiver)
        txn = build_txn("sim", sender, payee, amount, verdict, f)
        txn["status"] = {"allow": "paid", "caution": "paid", "pause": "held", "block": "blocked"}[
            verdict["decision"]]
        if txn["status"] == "held":
            txn["status_note"] = "Customer on a cooling-off check"
        if receiver is not None and txn["status"] == "paid":
            receiver.credit(sender["id"], sender["age"], amount)
        note_saved(receiver, txn)
        bank.add(txn)
        publish("txn", txn)


async def release_loop():
    """Cleared holds go through once the cooling-off time is over."""
    while True:
        await asyncio.sleep(2)
        for t in bank.recent(200):
            if t["source"] == "live" and t["status"] == "awaiting_guardian" and now() >= t["hold"]["until"]:
                bank.settle(t)
                t["status_note"] = "Released after the cooling-off time"
                publish("update", t)


# ---------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
async def console_page():
    return FileResponse(WEB / "console.html")


@app.get("/pay", include_in_schema=False)
async def pay_page():
    return FileResponse(WEB / "pay.html")


@app.get("/guardian", include_in_schema=False)
async def guardian_page():
    return FileResponse(WEB / "guardian.html")
