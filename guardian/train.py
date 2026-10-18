"""Train the coercion model and export it as plain JSON.

The exported file is a list of decision trees. The engine walks them with
plain Python, so scoring on LinuxONE needs no compiled ML wheels at all:
just Python, which ships on every s390x distro.

    python -m guardian.train --rows 1000000
"""

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import train_test_split

from .features import FEATURES
from .generator import generate

ROOT = Path(__file__).resolve().parent.parent


def export_trees(model):
    trees = []
    for it in model._predictors:
        nodes = it[0].nodes
        t = {"f": [], "t": [], "l": [], "r": [], "v": [], "leaf": []}
        for n in nodes:
            leaf = bool(n["is_leaf"])
            t["leaf"].append(1 if leaf else 0)
            t["f"].append(int(n["feature_idx"]))
            t["t"].append(round(float(n["num_threshold"]), 6) if not leaf else 0.0)
            t["l"].append(int(n["left"]))
            t["r"].append(int(n["right"]))
            t["v"].append(round(float(n["value"]), 8))
        trees.append(t)
    base = float(np.ravel(model._baseline_prediction)[0])
    return base, trees


def pure_predict(base, trees, x):
    raw = base
    for t in trees:
        i = 0
        while not t["leaf"][i]:
            i = t["l"][i] if x[t["f"][i]] <= t["t"][i] else t["r"][i]
        raw += t["v"][i]
    return 1 / (1 + math.exp(-raw))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--out", default=str(ROOT / "model" / "guardian_model.json"))
    args = ap.parse_args()

    t0 = time.time()
    X, y, persona = generate(args.rows)
    print(f"generated {len(y):,} rows ({y.mean()*100:.2f}% scam) in {time.time()-t0:.1f}s")

    Xtr, Xte, ytr, yte, _, pte = train_test_split(
        X, y, persona, test_size=0.2, random_state=1, stratify=y
    )

    t0 = time.time()
    model = HistGradientBoostingClassifier(
        max_iter=220,
        learning_rate=0.08,
        max_leaf_nodes=24,
        min_samples_leaf=80,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.1,
        random_state=3,
    )
    model.fit(Xtr, ytr)
    print(f"trained {model.n_iter_} trees in {time.time()-t0:.1f}s")

    prob = model.predict_proba(Xte)[:, 1]
    auc = roc_auc_score(yte, prob)
    ap_score = average_precision_score(yte, prob)

    metrics = {"rows": int(len(y)), "trees": int(model.n_iter_),
               "auc": round(float(auc), 4), "avg_precision": round(float(ap_score), 4)}
    print(f"AUC {auc:.4f}  avg precision {ap_score:.4f}")

    for cut in (0.4, 0.7):
        flag = prob >= cut
        tp = int((flag & (yte == 1)).sum())
        fp = int((flag & (yte == 0)).sum())
        fn = int((~flag & (yte == 1)).sum())
        prec = tp / max(tp + fp, 1)
        rec = tp / max(tp + fn, 1)
        fpr = fp / max(int((yte == 0).sum()), 1)
        metrics[f"at_{int(cut*100)}"] = {
            "precision": round(prec, 4), "recall": round(rec, 4), "false_alarm_rate": round(fpr, 5)
        }
        print(f"score>={int(cut*100)}: precision {prec:.3f} recall {rec:.3f} false alarms {fpr*100:.2f}% of genuine")

    per = {}
    for name in sorted(set(pte)):
        m = pte == name
        rate = float((prob[m] >= 0.7).mean())
        per[name] = round(rate, 4)
        print(f"  {name:18s} paused at 70+: {rate*100:5.1f}%")
    metrics["paused_share_by_persona"] = per

    base, trees = export_trees(model)

    # make sure the pure-python walk gives the same answer as sklearn
    sample = Xte[:2000]
    ref = model.predict_proba(sample)[:, 1]
    mine = np.array([pure_predict(base, trees, list(r)) for r in sample])
    drift = float(np.abs(ref - mine).max())
    print(f"export check: max difference {drift:.2e}")
    assert drift < 1e-5, "exported trees disagree with sklearn"

    out = {"version": 1, "features": FEATURES, "base": base, "trees": trees, "metrics": metrics}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, separators=(",", ":")))
    print(f"saved {args.out} ({Path(args.out).stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
