"""Hybrid decision engine: fixed rules first, the model for everything else.

Rules handle the cases where a person would never hesitate (a reported mule
account, a regular payment to family) and answer in microseconds. The model
handles the grey zone and explains itself.
"""

import json
import math
import time
from pathlib import Path

from .features import CALM, FEATURES, NOT_A_REASON, describe

MODEL_PATH = Path(__file__).resolve().parent.parent / "model" / "guardian_model.json"

ALLOW, CAUTION, PAUSE, BLOCK = "allow", "caution", "pause", "block"
CAUTION_AT, PAUSE_AT = 40, 70


PAYSIM_PATH = MODEL_PATH.parent / "paysim_model.json"


class Model:
    def __init__(self, path=MODEL_PATH, expected=FEATURES):
        data = json.loads(Path(path).read_text())
        if data["features"] != expected:
            raise ValueError("model was trained on a different feature list; retrain it")
        self.base = data["base"]
        self.metrics = data.get("metrics", {})
        # tuples are noticeably faster to index than lists in CPython
        self.trees = [
            (tuple(t["leaf"]), tuple(t["f"]), tuple(t["t"]), tuple(t["l"]), tuple(t["r"]), tuple(t["v"]))
            for t in data["trees"]
        ]

    def raw(self, x):
        total = self.base
        for leaf, f, th, left, right, val in self.trees:
            i = 0
            while not leaf[i]:
                i = left[i] if x[f[i]] <= th[i] else right[i]
            total += val[i]
        return total


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))


class Engine:
    def __init__(self, model_path=MODEL_PATH, paysim_path=PAYSIM_PATH):
        self.model = Model(model_path)
        self._idx = {f: i for i, f in enumerate(FEATURES)}
        self.paysim = None
        if Path(paysim_path).exists():
            from .train_paysim import FEATURES as PAYSIM_FEATURES
            self.paysim = Model(paysim_path, expected=PAYSIM_FEATURES)

    def paysim_score(self, amount, sender_balance, dest_balance, hour):
        """Transaction-pattern score (0-99) from the PaySim model, or None
        when that model has not been trained yet."""
        if self.paysim is None:
            return None
        from .train_paysim import live_features
        x = live_features(amount, sender_balance, dest_balance, hour)
        return min(round(_sigmoid(self.paysim.raw(x)) * 100), 99)

    def _vector(self, feats):
        missing = [f for f in FEATURES if f not in feats]
        if missing:
            raise ValueError(f"missing features: {', '.join(missing)}")
        return [float(feats[f]) for f in FEATURES]

    def _reasons(self, x, raw, limit=4):
        found = []
        for name in FEATURES:
            if name in NOT_A_REASON:
                continue
            i = self._idx[name]
            calm = CALM[name]
            if x[i] == calm:
                continue
            probe = list(x)
            probe[i] = calm
            lift = raw - self.model.raw(probe)
            if lift > 0.15:
                en, hi = describe(name, x[i])
                found.append({"signal": name, "en": en, "hi": hi, "weight": round(lift, 2)})
        found.sort(key=lambda r: r["weight"], reverse=True)
        return found[:limit]

    def decide(self, feats, receiver_reported=False, receiver_flag=None, message_verdict=None):
        """receiver_flag: set when another customer confirmed a scam to this
        account in the last 24h, e.g. {"minutes_ago": 3}.
        message_verdict: the AI message check on the text that came with this
        account number ("scam", "suspicious" or "looks_safe")."""
        start = time.perf_counter()
        x = self._vector(feats)
        g = lambda name: x[self._idx[name]]
        rules = []

        if receiver_reported:
            rules.append("Receiving account is on the 1930 reported-mule list")
            return self._result(BLOCK, 99, rules, [
                {"signal": "reported_mule", "en": "Account reported on the 1930 cyber helpline",
                 "hi": "Ye account 1930 pe fraud ke liye report hua hai", "weight": 9.9}
            ], "rules", start)

        if receiver_flag:
            mins = receiver_flag.get("minutes_ago", 0)
            when = "just now" if mins < 1 else f"{mins} min ago"
            rules.append(f"Another customer confirmed a scam to this account {when}")

        if message_verdict == "scam":
            rules.append("AI found a scam script in the message that came with this account")
        elif message_verdict == "suspicious":
            rules.append("AI found warning signs in the message that came with this account")

        fast_lane = not receiver_flag and message_verdict not in ("scam", "suspicious") and (
            g("payee_history") >= 3 and g("amount_ratio") <= 2.0 and g("balance_drain") < 0.3
            and g("app_switches") == 0 and g("pasted_account") == 0 and g("fd_broken_24h") == 0
            and g("retries_after_limit") == 0
        )
        if fast_lane:
            rules.append("Regular payee, usual amount, calm session")
            return self._result(ALLOW, 3, rules, [], "rules", start)

        raw = self.model.raw(x)
        score = round(_sigmoid(raw) * 100)

        floor = 0
        if g("retries_after_limit") >= 2 and g("new_payee") == 1 and g("balance_drain") >= 0.5:
            rules.append("Keeps retrying a large first-time payment after the limit")
            floor = max(floor, 85)
        if (g("recv_account_age_days") < 30 and g("recv_distinct_senders_24h") >= 5
                and g("recv_senior_share") >= 0.5):
            rules.append("Young account collecting money from many senior citizens")
            floor = max(floor, 90)
        if receiver_flag:
            floor = max(floor, 95)
        if message_verdict == "scam":
            floor = max(floor, 90)
        elif message_verdict == "suspicious":
            floor = max(floor, 55)
        score = max(score, floor)

        decision = PAUSE if score >= PAUSE_AT else CAUTION if score >= CAUTION_AT else ALLOW
        reasons = self._reasons(x, raw) if score >= CAUTION_AT else []
        return self._result(decision, score, rules, reasons, "rules+model" if rules else "model", start)

    @staticmethod
    def _result(decision, score, rules, reasons, path, start):
        return {
            "decision": decision,
            "score": min(int(score), 99),
            "rules": rules,
            "reasons": reasons,
            "path": path,
            "latency_us": int((time.perf_counter() - start) * 1_000_000),
        }
