"""A small in-memory bank, enough to run the demo end to end.

It keeps one customer who pays from the phone app, a registry of receiving
accounts with their recent credits, and every transaction the engine has
seen. Nothing here touches real money.
"""

import itertools
import os
import random
import threading
import time
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
DAY = 24 * 3600
COOLING_SECONDS = int(os.environ.get("GUARDIAN_COOLING_SECONDS", "120"))


def now():
    return time.time()


def ist_hour(ts):
    return datetime.fromtimestamp(ts, IST).hour


def mask(acct):
    acct = acct.replace(" ", "")
    return "xx" + acct[-4:] if len(acct) > 4 else acct


class Receiver:
    def __init__(self, account, name, age_days, forward_ratio=0.1, reported=False, kind="legit"):
        self.account = account
        self.name = name
        self.created = now() - age_days * DAY
        self.forward_ratio = forward_ratio
        self.reported = reported
        self.kind = kind
        self.credits = []   # (ts, sender_id, sender_age, amount)
        self.flag = None    # set when a customer confirms a scam to this account
        self.saved_after_flag = {"count": 0, "amount": 0}

    def flag_info(self):
        if not self.flag or now() - self.flag["ts"] > DAY:
            return None
        return {"minutes_ago": int((now() - self.flag["ts"]) // 60)}

    def raise_flag(self, by, reason):
        if self.flag is None:
            self.flag = {"ts": now(), "by": by, "reason": reason}
            return True
        return False

    def credit(self, sender_id, sender_age, amount, ts=None):
        self.credits.append((ts or now(), sender_id, sender_age, amount))

    def balance_today(self):
        cutoff = now() - DAY
        return sum(c[3] for c in self.credits if c[0] >= cutoff)

    def features(self):
        cutoff = now() - DAY
        recent = [c for c in self.credits if c[0] >= cutoff]
        self.credits = recent
        senders = {}
        for _, sid, age, _ in recent:
            senders[sid] = age
        seniors = sum(1 for a in senders.values() if a >= 60)
        return {
            "recv_account_age_days": max(int((now() - self.created) / DAY), 0),
            "recv_inbound_24h": len(recent),
            "recv_distinct_senders_24h": len(senders),
            "recv_senior_share": round(seniors / len(senders), 3) if senders else 0.0,
            "recv_forward_ratio": self.forward_ratio if recent else 0.0,
        }


class Bank:
    def __init__(self):
        self.lock = threading.RLock()
        self._ids = itertools.count(1001)
        self.reset()

    # ---- setup -----------------------------------------------------------
    def reset(self):
        with self.lock:
            self.customer = {
                "id": "C-0001",
                "name": "Suresh Sharma",
                "age": 66,
                "city": "Lucknow",
                "balance": 240000,
                "fd": 600000,
                "fd_broken_at": None,
                "avg_payment": 3200,
                "guardian": {"name": "Neha Sharma", "relation": "Daughter", "phone": "98xxxxx214"},
            }
            self.payees = {
                "50100221847301": {"name": "Neha Sharma", "note": "Daughter", "history": 14},
                "40220018833012": {"name": "Gupta General Store", "note": "Monthly ration", "history": 41},
                "30098811220045": {"name": "Electricity bill", "note": "Auto-pay", "history": 12},
            }
            self.receivers = {}
            for acct, p in self.payees.items():
                self.receivers[acct] = Receiver(acct, p["name"], random.randint(900, 4000))
            # accounts the simulator feeds and the demo kit points at
            # the demo kit account: a mule on its first day, so it has no
            # history yet and looks clean. Only behaviour can catch the first
            # payment; the community flag catches the ones after it.
            self.receivers["50100447122906"] = Receiver(
                "50100447122906", "R K Enterprises", 640, 0.2, kind="sleeper")
            mules = [
                ("91802265530417", "Shree Balaji Traders", 19, 0.78),
                ("33450011987260", "Arjun Kumar", 640, 0.81),   # aged account bought from someone
            ]
            for acct, name, age, fwd in mules:
                self.receivers[acct] = Receiver(acct, name, age, fwd, kind="mule")
            self.receivers["77001234500981"] = Receiver(
                "77001234500981", "Verma Logistics", 30, 0.9, reported=True, kind="mule")
            # a few seniors already paid this mule today
            for i in range(4):
                self.receivers["91802265530417"].credit(f"S-seed{i}", random.randint(61, 79),
                                                        random.randint(90000, 400000),
                                                        now() - random.randint(600, 18000))
            self.txns = {}
            self.order = []

    # ---- helpers ---------------------------------------------------------
    def receiver(self, account, name=None):
        account = account.replace(" ", "")
        r = self.receivers.get(account)
        if r is None:
            r = Receiver(account, name or "New account", random.randint(400, 3000))
            self.receivers[account] = r
        return r

    def available(self):
        c = self.customer
        return c["balance"]

    def fd_broken_recently(self):
        t = self.customer["fd_broken_at"]
        return 1 if t and now() - t < DAY else 0

    def break_fd(self):
        with self.lock:
            c = self.customer
            if c["fd"] <= 0:
                return False
            c["balance"] += c["fd"]
            c["fd"] = 0
            c["fd_broken_at"] = now()
            return True

    def next_id(self):
        return f"GP{next(self._ids)}"

    def add(self, txn):
        with self.lock:
            self.txns[txn["id"]] = txn
            self.order.append(txn["id"])
            if len(self.order) > 600:
                old = self.order.pop(0)
                self.txns.pop(old, None)

    def get(self, txn_id):
        return self.txns.get(txn_id)

    def recent(self, n=80):
        with self.lock:
            return [self.txns[i] for i in self.order[-n:] if i in self.txns]

    # ---- money movement for the live customer ----------------------------
    def settle(self, txn):
        """Move the money for a phone payment that has been cleared."""
        with self.lock:
            acct = txn["payee"]["account"]
            amt = txn["amount"]
            if amt > self.customer["balance"]:
                txn["status"] = "failed"
                txn["status_note"] = "Not enough balance"
                return False
            self.customer["balance"] -= amt
            self.receiver(acct).credit(self.customer["id"], self.customer["age"], amt)
            p = self.payees.get(acct)
            if p:
                p["history"] += 1
            else:
                self.payees[acct] = {"name": txn["payee"]["name"], "note": "Added today", "history": 1}
            txn["status"] = "paid"
            return True

    def flagged(self):
        out = []
        for r in self.receivers.values():
            if r.flag:
                out.append({
                    "account": r.account, "masked": mask(r.account), "name": r.name,
                    "ts": r.flag["ts"], "by": r.flag["by"], "reason": r.flag["reason"],
                    "saved_count": r.saved_after_flag["count"],
                    "saved_amount": r.saved_after_flag["amount"],
                })
        return sorted(out, key=lambda f: f["ts"], reverse=True)

    def stats(self):
        with self.lock:
            items = [self.txns[i] for i in self.order if i in self.txns]
        total = len(items)
        paused = sum(1 for t in items if t["decision"] == "pause")
        blocked = sum(1 for t in items if t["decision"] == "block")
        saved = sum(t["amount"] for t in items
                    if t["decision"] in ("pause", "block") and t["status"] != "paid")
        lat = sorted(t["latency_us"] for t in items) or [0]
        return {
            "scored": total,
            "paused": paused,
            "blocked": blocked,
            "saved": saved,
            "latency_median_us": lat[len(lat) // 2],
            "latency_p99_us": lat[min(len(lat) - 1, int(len(lat) * 0.99))],
        }


bank = Bank()
