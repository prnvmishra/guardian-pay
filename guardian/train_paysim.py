"""Second model: transaction patterns learned from the public PaySim dataset.

PaySim (Lopez-Rojas et al., 2016) is a mobile-money simulator calibrated on
the real transaction logs of a mobile money operator. Its Kaggle release has
6.3 million transactions with fraud labels. Download it from
https://www.kaggle.com/datasets/ealaxi/paysim1 and run:

    python -m guardian.train_paysim --csv path/to/PS_20174392719_1491204439457_log.csv

Only facts that exist *before* the money moves are used (the post-transaction
balances in PaySim leak the label), and amounts enter as ratios so the model
transfers from PaySim's currency to rupees. Fraud in PaySim only happens in
TRANSFER and CASH_OUT, so those are the rows we learn from.
"""

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "model" / "paysim_model.json"

FEATURES = [
    "is_transfer",        # TRANSFER (1) or CASH_OUT (0)
    "drain",              # amount / sender balance before (capped at 2)
    "empties_account",    # amount takes (almost) everything the sender had
    "sender_was_empty",   # sender balance was 0 before
    "dest_was_empty",     # receiving account balance was 0 before
    "log_dest_balance",   # log10(1 + receiving balance before)
    "hour",               # hour of day (step % 24)
]


def featurize(df):
    amt = df["amount"].to_numpy(float)
    ob = df["oldbalanceOrg"].to_numpy(float)
    od = df["oldbalanceDest"].to_numpy(float)
    X = np.column_stack([
        (df["type"].to_numpy() == "TRANSFER").astype(float),
        np.clip(amt / np.maximum(ob, 1.0), 0, 2),
        (amt >= 0.99 * ob).astype(float) * (ob > 0),
        (ob <= 0).astype(float),
        (od <= 0).astype(float),
        np.log10(1 + np.maximum(od, 0)),
        (df["step"].to_numpy() % 24).astype(float),
    ])
    return X


def live_features(amount, sender_balance, dest_balance, hour):
    """The same seven facts for a payment made in our app."""
    return [
        1.0,
        min(amount / max(sender_balance, 1.0), 2.0),
        1.0 if sender_balance > 0 and amount >= 0.99 * sender_balance else 0.0,
        1.0 if sender_balance <= 0 else 0.0,
        1.0 if dest_balance <= 0 else 0.0,
        math.log10(1 + max(dest_balance, 0)),
        float(hour),
    ]


def load(csv, nrows=None):
    import pandas as pd

    cols = ["step", "type", "amount", "oldbalanceOrg", "oldbalanceDest", "isFraud"]
    df = pd.read_csv(csv, usecols=cols, nrows=nrows,
                     dtype={"step": "int32", "type": "category", "amount": "float64",
                            "oldbalanceOrg": "float64", "oldbalanceDest": "float64", "isFraud": "int8"})
    missing = set(cols) - set(df.columns)
    if missing:
        raise SystemExit(f"This is not the PaySim file: missing {', '.join(sorted(missing))}")
    return df[df["type"].isin(["TRANSFER", "CASH_OUT"])].reset_index(drop=True)


def main(argv=None):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import average_precision_score, roc_auc_score

    from .train import export_trees, pure_predict

    ap = argparse.ArgumentParser(description="Train the PaySim transaction-pattern model")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--nrows", type=int, default=None, help="read only the first N rows (for a quick run)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    t0 = time.time()
    df = load(args.csv, args.nrows)
    print(f"{len(df):,} TRANSFER/CASH_OUT rows, {df['isFraud'].mean()*100:.3f}% fraud ({time.time()-t0:.1f}s)")

    # split by time, like a bank would: learn from the past, test on later days
    cut = int(np.quantile(df["step"], 0.75))
    train, test = df[df["step"] <= cut], df[df["step"] > cut]
    if test["isFraud"].nunique() < 2 or train["isFraud"].nunique() < 2:
        raise SystemExit("Not enough fraud cases on both sides of the time split; use more rows")
    Xtr, ytr = featurize(train), train["isFraud"].to_numpy()
    Xte, yte = featurize(test), test["isFraud"].to_numpy()

    model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.08, max_leaf_nodes=24,
                                           min_samples_leaf=60, l2_regularization=1.0,
                                           early_stopping=True, validation_fraction=0.1, random_state=3)
    model.fit(Xtr, ytr)
    prob = model.predict_proba(Xte)[:, 1]
    auc = float(roc_auc_score(yte, prob))
    ap_score = float(average_precision_score(yte, prob))
    flag = prob >= 0.8
    tp = int((flag & (yte == 1)).sum())
    fp = int((flag & (yte == 0)).sum())
    metrics = {
        "dataset": "PaySim (Lopez-Rojas et al.; Kaggle ealaxi/paysim1, same file mirrored at huggingface.co/datasets/theman10/paysim)",
        "rows_used": int(len(df)),
        "train_steps": f"1-{cut}", "test_steps": f"{cut + 1}-{int(df['step'].max())}",
        "trees": int(model.n_iter_),
        "auc": round(auc, 4), "avg_precision": round(ap_score, 4),
        "at_80": {"precision": round(tp / max(tp + fp, 1), 4),
                  "recall": round(tp / max(int((yte == 1).sum()), 1), 4)},
    }
    print(json.dumps(metrics, indent=1))

    base, trees = export_trees(model)
    sample = Xte[:2000]
    drift = float(np.abs(model.predict_proba(sample)[:, 1] -
                         np.array([pure_predict(base, trees, list(r)) for r in sample])).max())
    assert drift < 1e-5, "exported trees disagree with sklearn"
    Path(args.out).write_text(json.dumps({"version": 1, "features": FEATURES, "base": base,
                                          "trees": trees, "metrics": metrics}, separators=(",", ":")))
    print(f"saved {args.out}")
    return metrics


if __name__ == "__main__":
    main()
