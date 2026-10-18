"""Checks the PaySim training pipeline end to end on a tiny file with the
same columns as the real Kaggle CSV. The numbers here are a test fixture,
not training data; the shipped model is trained on the real file."""

import numpy as np
import pandas as pd

from guardian.engine import Engine
from guardian.train_paysim import FEATURES, live_features, main


def fixture_csv(path, n=6000, seed=0):
    r = np.random.default_rng(seed)
    step = np.sort(r.integers(1, 744, n))
    typ = r.choice(["PAYMENT", "TRANSFER", "CASH_OUT", "CASH_IN", "DEBIT"], n, p=[.3, .25, .3, .1, .05])
    ob = r.lognormal(10, 1.5, n)
    amt = ob * r.uniform(0.01, 0.4, n)
    od = r.lognormal(11, 2, n)
    fraud = (r.random(n) < 0.03) & np.isin(typ, ["TRANSFER", "CASH_OUT"])
    amt = np.where(fraud, ob, amt)
    od = np.where(fraud & (typ == "TRANSFER"), 0, od)
    df = pd.DataFrame({
        "step": step, "type": typ, "amount": amt.round(2), "nameOrig": [f"C{i}" for i in range(n)],
        "oldbalanceOrg": ob.round(2), "newbalanceOrig": (ob - amt).round(2),
        "nameDest": [f"C{i+n}" for i in range(n)], "oldbalanceDest": od.round(2),
        "newbalanceDest": (od + amt).round(2), "isFraud": fraud.astype(int), "isFlaggedFraud": 0,
    })
    df.to_csv(path, index=False)


def test_pipeline_trains_exports_and_loads(tmp_path):
    csv = tmp_path / "paysim.csv"
    out = tmp_path / "paysim_model.json"
    fixture_csv(csv)
    metrics = main(["--csv", str(csv), "--out", str(out)])
    assert metrics["auc"] > 0.9
    eng = Engine(paysim_path=out)
    risky = eng.paysim_score(500000, 500000, 0, 2)
    normal = eng.paysim_score(2000, 240000, 900000, 14)
    assert risky > normal
    assert 0 <= normal <= 99


def test_live_features_shape():
    assert len(live_features(100, 1000, 0, 10)) == len(FEATURES)
    assert live_features(1000, 1000, 0, 3)[2] == 1.0   # empties the account
