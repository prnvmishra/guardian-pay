"""Synthetic payment generator.

No bank will hand real scam data to a hackathon team, so we model the
situations we care about as personas and sample from them. The patterns come
from public advisories on 'digital arrest', relative-impersonation and task
scams (NPCI, I4C, RBI). Overlap between personas is deliberate: an anxious
70 year old paying their daughter looks a lot like a coerced one, and the
model has to learn the difference instead of memorising one flag.

    python -m guardian.generator --rows 1000000 --out data/payments.csv.gz
"""

import argparse
import time

import numpy as np

from .features import FEATURES

PERSONAS = {
    # name: (share, is_scam)
    "everyday":           (0.680, 0),
    "big_legit":          (0.120, 0),  # rent, gadget, fees: new payee, big amount, calm
    "family_emergency":   (0.050, 0),  # real relative, known payee, rushed
    "anxious_senior":     (0.060, 0),  # slow, hesitant, but paying someone they know
    "new_merchant":       (0.040, 0),  # young legit receiving account
    "digital_arrest":     (0.025, 1),
    "relative_clone":     (0.015, 1),  # "Papa, accident ho gaya" voice-clone calls
    "task_scam":          (0.010, 1),  # part-time job / trading group, repeated top-ups
}


def _clip(a, lo, hi):
    return np.clip(a, lo, hi)


def _persona(name, n, rng):
    r = rng
    d = {}
    if name == "everyday":
        d["app_switches"] = r.poisson(0.15, n)
        d["pasted_account"] = r.random(n) < 0.04
        d["confirm_hesitation_s"] = r.gamma(2.0, 1.8, n)
        d["amount_edits"] = r.poisson(0.1, n)
        d["retries_after_limit"] = np.zeros(n)
        d["session_duration_s"] = r.gamma(3.0, 9.0, n)
        d["amount_ratio"] = r.lognormal(-0.1, 0.55, n)
        d["balance_drain"] = r.beta(1.2, 30, n)
        d["new_payee"] = r.random(n) < 0.08
        d["fd_broken_24h"] = r.random(n) < 0.002
        d["night_hour"] = r.random(n) < 0.08
        d["sender_age"] = r.integers(19, 80, n)
        recv = "established"
    elif name == "big_legit":
        d["app_switches"] = r.poisson(0.5, n)
        d["pasted_account"] = r.random(n) < 0.35
        d["confirm_hesitation_s"] = r.gamma(2.5, 3.0, n)
        d["amount_edits"] = r.poisson(0.4, n)
        d["retries_after_limit"] = r.poisson(0.15, n)
        d["session_duration_s"] = r.gamma(3.0, 18.0, n)
        d["amount_ratio"] = r.lognormal(2.0, 0.7, n)
        d["balance_drain"] = r.beta(2.0, 5.0, n)
        d["new_payee"] = r.random(n) < 0.7
        d["fd_broken_24h"] = r.random(n) < 0.05
        d["night_hour"] = r.random(n) < 0.05
        d["sender_age"] = r.integers(22, 75, n)
        recv = "established"
    elif name == "family_emergency":
        d["app_switches"] = r.poisson(1.2, n)
        d["pasted_account"] = r.random(n) < 0.15
        d["confirm_hesitation_s"] = r.gamma(2.0, 2.5, n)
        d["amount_edits"] = r.poisson(0.5, n)
        d["retries_after_limit"] = r.poisson(0.3, n)
        d["session_duration_s"] = r.gamma(3.0, 15.0, n)
        d["amount_ratio"] = r.lognormal(1.8, 0.7, n)
        d["balance_drain"] = r.beta(2.0, 4.0, n)
        d["new_payee"] = r.random(n) < 0.08
        d["fd_broken_24h"] = r.random(n) < 0.12
        d["night_hour"] = r.random(n) < 0.25
        d["sender_age"] = r.integers(35, 82, n)
        recv = "established"
    elif name == "anxious_senior":
        d["app_switches"] = r.poisson(0.9, n)
        d["pasted_account"] = r.random(n) < 0.05
        d["confirm_hesitation_s"] = r.gamma(3.0, 6.0, n)
        d["amount_edits"] = r.poisson(1.2, n)
        d["retries_after_limit"] = r.poisson(0.1, n)
        d["session_duration_s"] = r.gamma(4.0, 25.0, n)
        d["amount_ratio"] = r.lognormal(0.3, 0.7, n)
        d["balance_drain"] = r.beta(1.5, 12, n)
        d["new_payee"] = r.random(n) < 0.06
        d["fd_broken_24h"] = r.random(n) < 0.02
        d["night_hour"] = r.random(n) < 0.04
        d["sender_age"] = r.integers(60, 88, n)
        recv = "established"
    elif name == "new_merchant":
        d["app_switches"] = r.poisson(0.4, n)
        d["pasted_account"] = r.random(n) < 0.3
        d["confirm_hesitation_s"] = r.gamma(2.0, 2.0, n)
        d["amount_edits"] = r.poisson(0.2, n)
        d["retries_after_limit"] = np.zeros(n)
        d["session_duration_s"] = r.gamma(3.0, 12.0, n)
        d["amount_ratio"] = r.lognormal(0.4, 0.6, n)
        d["balance_drain"] = r.beta(1.2, 20, n)
        d["new_payee"] = r.random(n) < 0.85
        d["fd_broken_24h"] = r.random(n) < 0.003
        d["night_hour"] = r.random(n) < 0.06
        d["sender_age"] = r.integers(18, 70, n)
        recv = "young_legit"
    elif name == "digital_arrest":
        d["app_switches"] = r.poisson(3.2, n)
        d["pasted_account"] = r.random(n) < 0.8
        d["confirm_hesitation_s"] = r.gamma(3.0, 5.0, n)
        d["amount_edits"] = r.poisson(0.8, n)
        d["retries_after_limit"] = r.poisson(1.1, n)
        d["session_duration_s"] = r.gamma(4.0, 35.0, n)
        d["amount_ratio"] = r.lognormal(3.0, 0.8, n)
        d["balance_drain"] = r.beta(6.0, 1.8, n)
        d["new_payee"] = r.random(n) < 0.95
        d["fd_broken_24h"] = r.random(n) < 0.45
        d["night_hour"] = r.random(n) < 0.15
        d["sender_age"] = r.integers(45, 85, n)
        recv = "mule"
    elif name == "relative_clone":
        d["app_switches"] = r.poisson(1.6, n)
        d["pasted_account"] = r.random(n) < 0.75
        d["confirm_hesitation_s"] = r.gamma(1.8, 2.0, n)   # rushed, not hesitant
        d["amount_edits"] = r.poisson(0.3, n)
        d["retries_after_limit"] = r.poisson(0.5, n)
        d["session_duration_s"] = r.gamma(3.0, 14.0, n)
        d["amount_ratio"] = r.lognormal(2.0, 0.7, n)
        d["balance_drain"] = r.beta(2.5, 4.0, n)
        d["new_payee"] = r.random(n) < 0.92
        d["fd_broken_24h"] = r.random(n) < 0.08
        d["night_hour"] = r.random(n) < 0.35
        d["sender_age"] = r.integers(40, 85, n)
        recv = "mule"
    elif name == "task_scam":
        d["app_switches"] = r.poisson(2.2, n)
        d["pasted_account"] = r.random(n) < 0.9
        d["confirm_hesitation_s"] = r.gamma(2.0, 2.5, n)
        d["amount_edits"] = r.poisson(0.4, n)
        d["retries_after_limit"] = r.poisson(0.6, n)
        d["session_duration_s"] = r.gamma(3.0, 12.0, n)
        d["amount_ratio"] = r.lognormal(1.3, 0.6, n)
        d["balance_drain"] = r.beta(2.0, 4.0, n)
        d["new_payee"] = r.random(n) < 0.6     # same mule paid again across top-ups
        d["fd_broken_24h"] = r.random(n) < 0.04
        d["night_hour"] = r.random(n) < 0.3
        d["sender_age"] = r.integers(19, 45, n)
        recv = "mule_young_victims"
    else:
        raise ValueError(name)

    d["payee_history"] = np.where(d["new_payee"], 0, r.poisson(9, n) + 1)
    d.update(_receiver(recv, n, r))
    if recv.startswith("mule"):
        # a mule account on its first day of use has no history yet, so the
        # receiver side looks clean and only the sender's behaviour gives it
        # away. Without these rows the model would lean on the receiver alone.
        fresh = r.random(n) < 0.4
        clean = _receiver("established", n, r)
        for k, v in clean.items():
            d[k] = np.where(fresh, v, d[k])
    return d


def _receiver(kind, n, r):
    if kind == "established":
        return {
            "recv_account_age_days": r.integers(200, 5000, n),
            "recv_inbound_24h": r.poisson(3, n),
            "recv_distinct_senders_24h": r.poisson(2, n),
            "recv_senior_share": r.beta(1.5, 6, n),
            "recv_forward_ratio": r.beta(1.2, 6, n),
        }
    if kind == "young_legit":
        return {
            "recv_account_age_days": r.integers(5, 120, n),
            "recv_inbound_24h": r.poisson(14, n),
            "recv_distinct_senders_24h": r.poisson(11, n),
            "recv_senior_share": r.beta(1.5, 5, n),
            "recv_forward_ratio": r.beta(1.5, 5, n),
        }
    if kind == "mule":
        # some mules are aged accounts bought from real people, so age alone
        # is not enough
        aged = r.random(n) < 0.3
        return {
            "recv_account_age_days": np.where(aged, r.integers(200, 2500, n), r.integers(1, 60, n)),
            "recv_inbound_24h": r.poisson(7, n),
            "recv_distinct_senders_24h": r.poisson(5, n),
            "recv_senior_share": r.beta(5, 2, n),
            "recv_forward_ratio": r.beta(6, 1.5, n),
        }
    if kind == "mule_young_victims":
        aged = r.random(n) < 0.3
        return {
            "recv_account_age_days": np.where(aged, r.integers(200, 2500, n), r.integers(1, 60, n)),
            "recv_inbound_24h": r.poisson(9, n),
            "recv_distinct_senders_24h": r.poisson(6, n),
            "recv_senior_share": r.beta(1.2, 6, n),
            "recv_forward_ratio": r.beta(6, 1.5, n),
        }
    raise ValueError(kind)


def generate(rows, seed=7):
    """Return (X, y, persona) as numpy arrays."""
    rng = np.random.default_rng(seed)
    names = list(PERSONAS)
    shares = np.array([PERSONAS[k][0] for k in names])
    counts = rng.multinomial(rows, shares / shares.sum())

    cols = {f: [] for f in FEATURES}
    labels, persona = [], []
    for name, n in zip(names, counts):
        if n == 0:
            continue
        part = _persona(name, n, rng)
        for f in FEATURES:
            cols[f].append(np.asarray(part[f], dtype=float))
        labels.append(np.full(n, PERSONAS[name][1]))
        persona.append(np.full(n, name, dtype=object))

    X = np.column_stack([np.concatenate(cols[f]) for f in FEATURES])
    y = np.concatenate(labels).astype(int)
    p = np.concatenate(persona)

    # keep values inside sane ranges
    idx = {f: i for i, f in enumerate(FEATURES)}
    X[:, idx["confirm_hesitation_s"]] = _clip(X[:, idx["confirm_hesitation_s"]], 0.5, 300)
    X[:, idx["session_duration_s"]] = _clip(X[:, idx["session_duration_s"]], 5, 1800)
    X[:, idx["amount_ratio"]] = _clip(X[:, idx["amount_ratio"]], 0.05, 400)
    X[:, idx["balance_drain"]] = _clip(X[:, idx["balance_drain"]], 0.0, 1.0)

    # a little label noise: reporting is never perfect
    flip = rng.random(rows) < 0.003
    y = np.where(flip, 1 - y, y)

    order = rng.permutation(rows)
    return X[order], y[order], p[order]


def sample_one(rng, persona):
    """Single row as a dict, used by the live simulator."""
    row = _persona(persona, 1, rng)
    out = {}
    for f in FEATURES:
        v = np.asarray(row[f]).ravel()[0]
        out[f] = float(v)
    out["confirm_hesitation_s"] = min(max(out["confirm_hesitation_s"], 0.5), 300)
    out["session_duration_s"] = min(max(out["session_duration_s"], 5), 1800)
    out["amount_ratio"] = min(max(out["amount_ratio"], 0.05), 400)
    out["balance_drain"] = min(max(out["balance_drain"], 0.0), 1.0)
    return out


def main():
    ap = argparse.ArgumentParser(description="Generate synthetic payments")
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/payments.csv.gz")
    args = ap.parse_args()

    t = time.time()
    X, y, p = generate(args.rows, args.seed)
    import pandas as pd

    df = pd.DataFrame(X, columns=FEATURES)
    df["persona"] = p
    df["is_scam"] = y
    df.to_csv(args.out, index=False, float_format="%.4g")
    print(f"{len(df):,} rows, {y.mean()*100:.2f}% scam, {time.time()-t:.1f}s -> {args.out}")


if __name__ == "__main__":
    main()
