"""Sentence-level text model for scam and pressure detection.

Reads a whole message or a customer's answer, not a keyword list. Features are
word 1-2 grams plus character 2-5 grams, so it copes with Hinglish spelling
("giraftar", "griftar", "girftaar") and with Devanagari. Features are hashed,
which keeps the exported model small and lets inference run in plain Python
on LinuxONE without scikit-learn.

Heads (each a logistic regression):
    scam        is this a scam / pressure message            (all sources)
    category    digital arrest, KYC, prize, ...               (sources that label it)
    urgency     low / medium / high                           (sources that label it)
    blackmail   threat or blackmail present                   (sources that label it)

Train:
    python -m guardian.text_model --data data/text
"""

import argparse
import json
import math
import re
import time
import unicodedata
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "model" / "text_model.json"
DIM = 1 << 18

_URL = re.compile(r"(https?://\S+|www\.\S+|\b\S+\.(?:com|in|net|org|xyz|top|link|ly)\b\S*)", re.I)
_LONGNUM = re.compile(r"\d[\d\s-]{6,}\d")
_PHONE = re.compile(r"(\+?\d{1,3}[\s-]?)?\d{10}\b")
_MONEY = re.compile(r"(rs\.?|inr|₹)\s*[\d,]+|\b\d[\d,]*\s*(rs|rupees?|rupaye|lakh|crore|हज़ार|लाख)\b", re.I)
_DIGITS = re.compile(r"\d")
_TOKEN = re.compile(r"[\wऀ-ॿ]+", re.U)


def normalize(text):
    t = unicodedata.normalize("NFKC", str(text)).lower()
    t = _URL.sub(" xurlx ", t)
    t = _MONEY.sub(" xmoneyx ", t)
    t = _LONGNUM.sub(" xacctx ", t)
    t = _PHONE.sub(" xphonex ", t)
    t = _DIGITS.sub("0", t)
    return t


def _h(s):
    return zlib.crc32(s.encode("utf-8")) & (DIM - 1)


def features(text):
    """Sparse feature dict {index: value}, L2-normalised log counts."""
    t = normalize(text)
    words = _TOKEN.findall(t)
    counts = {}

    def add(key):
        i = _h(key)
        counts[i] = counts.get(i, 0) + 1

    for w in words:
        add("w:" + w)
    for a, b in zip(words, words[1:]):
        add("b:" + a + " " + b)
    for w in words:
        padded = f" {w} "
        for n in (2, 3, 4, 5):
            if len(padded) < n:
                continue
            for k in range(len(padded) - n + 1):
                add("c:" + padded[k:k + n])
    if not counts:
        return {}
    vals = {i: 1.0 + math.log(c) for i, c in counts.items()}
    norm = math.sqrt(sum(v * v for v in vals.values()))
    return {i: v / norm for i, v in vals.items()}


# ------------------------------------------------------------------ inference
class TextModel:
    def __init__(self, path=MODEL_PATH):
        data = json.loads(Path(path).read_text())
        self.metrics = data.get("metrics", {})
        self.heads = {}
        for name, h in data["heads"].items():
            weights = [{int(k): v for k, v in w.items()} for w in h["weights"]]
            self.heads[name] = (h["classes"], weights, h["bias"])

    def _scores(self, head, x):
        classes, weights, bias = self.heads[head]
        z = [b + sum(w.get(i, 0.0) * v for i, v in x.items()) for w, b in zip(weights, bias)]
        if len(classes) == 2 and len(z) == 1:
            p = 1 / (1 + math.exp(-z[0]))
            return {classes[0]: 1 - p, classes[1]: p}
        m = max(z)
        e = [math.exp(v - m) for v in z]
        s = sum(e)
        return {c: v / s for c, v in zip(classes, e)}

    def predict(self, text):
        x = features(text)
        out = {"scam_probability": round(self._scores("scam", x)[1], 4)}
        for head in ("category", "urgency", "blackmail"):
            if head in self.heads:
                probs = self._scores(head, x)
                best = max(probs, key=probs.get)
                out[head] = {"label": best, "probability": round(probs[best], 4)}
        return out


def load_default():
    return TextModel() if MODEL_PATH.exists() else None


# ------------------------------------------------------------------ training data
def load_sources(folder):
    """Each loader returns a list of dicts: text, scam (0/1), source, and
    optional category / urgency / blackmail."""
    import pandas as pd

    folder = Path(folder)
    rows = []

    p = folder / "uci_sms_spam.tsv"
    if p.exists():
        df = pd.read_csv(p, sep="\t", header=None, names=["label", "text"], quoting=3)
        for t, l in zip(df["text"], df["label"]):
            rows.append({"text": str(t), "scam": int(l == "spam"), "source": "uci_sms"})

    p = folder / "hinglish_calls.csv"
    if p.exists():
        df = pd.read_csv(p)
        for r in df.itertuples(index=False):
            d = r._asdict()
            row = {"text": str(d["text"]), "scam": int(d["label"]), "source": "hinglish_calls"}
            if isinstance(d.get("scam_category"), str):
                row["category"] = d["scam_category"] if int(d["label"]) == 1 else None
            if isinstance(d.get("urgency_level"), str):
                row["urgency"] = d["urgency_level"]
            if "contains_blackmail" in d and d["contains_blackmail"] == d["contains_blackmail"]:
                row["blackmail"] = int(bool(d["contains_blackmail"]) if not isinstance(d["contains_blackmail"], str)
                                       else d["contains_blackmail"].lower() == "true")
            rows.append(row)

    p = folder / "india_14lang.csv"
    if p.exists():
        df = pd.read_csv(p)
        for t, l in zip(df["text"], df["label"]):
            rows.append({"text": str(t), "scam": int(l), "source": "india_14lang"})

    p = folder / "hinglish_scam_text.json"
    if p.exists():
        raw = json.loads(p.read_text())
        items = raw.get("rows", raw) if isinstance(raw, dict) else raw
        for it in items:
            it = it.get("row", it)
            if "text" in it and "label" in it:
                rows.append({"text": str(it["text"]), "scam": int(it["label"]), "source": "hinglish_scam_text"})

    # drop empties and exact duplicates (keeps a test set honest)
    seen, clean = set(), []
    for r in rows:
        key = normalize(r["text"]).strip()
        if len(key) < 3 or key in seen:
            continue
        seen.add(key)
        clean.append(r)
    return clean


# ------------------------------------------------------------------ training
def _matrix(texts):
    from scipy.sparse import csr_matrix

    indptr, indices, data = [0], [], []
    for t in texts:
        f = features(t)
        indices.extend(f.keys())
        data.extend(f.values())
        indptr.append(len(indices))
    return csr_matrix((data, indices, indptr), shape=(len(texts), DIM), dtype="float64")


def _fit(X, y, C=8.0):
    from sklearn.linear_model import LogisticRegression

    clf = LogisticRegression(C=C, max_iter=3000, class_weight="balanced", solver="liblinear"
                             if len(set(y)) == 2 else "lbfgs")
    clf.fit(X, y)
    return clf


def _export(clf, prune=1e-4):
    weights = []
    for row in clf.coef_:
        weights.append({str(i): round(float(v), 5) for i, v in enumerate(row) if abs(v) > prune})
    return {"classes": [c.item() if hasattr(c, "item") else c for c in clf.classes_],
            "weights": weights, "bias": [round(float(b), 5) for b in clf.intercept_]}


def _binary_report(y, p, cut=0.5):
    from sklearn.metrics import average_precision_score, f1_score, roc_auc_score

    pred = [1 if v >= cut else 0 for v in p]
    tp = sum(1 for a, b in zip(y, pred) if a == b == 1)
    fp = sum(1 for a, b in zip(y, pred) if a == 0 and b == 1)
    fn = sum(1 for a, b in zip(y, pred) if a == 1 and b == 0)
    return {"n": len(y), "auc": round(float(roc_auc_score(y, p)), 4),
            "avg_precision": round(float(average_precision_score(y, p)), 4),
            "precision": round(tp / max(tp + fp, 1), 4), "recall": round(tp / max(tp + fn, 1), 4),
            "f1": round(float(f1_score(y, pred)), 4)}


def train(folder, out=MODEL_PATH, seed=11):
    import random

    from sklearn.metrics import accuracy_score, f1_score

    rows = load_sources(folder)
    if not rows:
        raise SystemExit(f"No training data found in {folder}")
    sources = sorted({r["source"] for r in rows})
    print(f"{len(rows):,} unique texts from {', '.join(sources)}")

    rng = random.Random(seed)
    rng.shuffle(rows)
    cut = int(len(rows) * 0.8)
    train_rows, test_rows = rows[:cut], rows[cut:]
    metrics = {"sources": {s: sum(1 for r in rows if r["source"] == s) for s in sources},
               "train_size": len(train_rows), "test_size": len(test_rows)}

    t0 = time.time()
    Xtr = _matrix([r["text"] for r in train_rows])
    Xte = _matrix([r["text"] for r in test_rows])
    heads = {}

    scam = _fit(Xtr, [r["scam"] for r in train_rows])
    heads["scam"] = _export(scam)
    p = scam.predict_proba(Xte)[:, 1]
    metrics["scam_holdout"] = _binary_report([r["scam"] for r in test_rows], list(p))
    metrics["scam_holdout_by_source"] = {}
    for s in sources:
        idx = [i for i, r in enumerate(test_rows) if r["source"] == s]
        ys = [test_rows[i]["scam"] for i in idx]
        if len(set(ys)) == 2:
            metrics["scam_holdout_by_source"][s] = _binary_report(ys, [p[i] for i in idx])

    # cross-source: train without a source, test on it. Shows whether the
    # model learned scams or just one dataset's style.
    metrics["scam_unseen_source"] = {}
    if len(sources) > 1:
        for s in sources:
            tr = [r for r in rows if r["source"] != s]
            te = [r for r in rows if r["source"] == s]
            if len({r["scam"] for r in te}) < 2 or len({r["scam"] for r in tr}) < 2:
                continue
            clf = _fit(_matrix([r["text"] for r in tr]), [r["scam"] for r in tr])
            pp = clf.predict_proba(_matrix([r["text"] for r in te]))[:, 1]
            metrics["scam_unseen_source"][s] = _binary_report([r["scam"] for r in te], list(pp))

    for head in ("category", "urgency", "blackmail"):
        tr = [r for r in train_rows if r.get(head) not in (None, "")]
        te = [r for r in test_rows if r.get(head) not in (None, "")]
        if len(tr) < 200 or len({r[head] for r in tr}) < 2:
            continue
        clf = _fit(_matrix([r["text"] for r in tr]), [r[head] for r in tr])
        heads[head] = _export(clf)
        if te:
            pred = clf.predict(_matrix([r["text"] for r in te]))
            ys = [r[head] for r in te]
            metrics[f"{head}_holdout"] = {"n": len(te), "accuracy": round(float(accuracy_score(ys, pred)), 4),
                                          "macro_f1": round(float(f1_score(ys, pred, average="macro")), 4)}

    metrics["train_seconds"] = round(time.time() - t0, 1)
    payload = {"version": 1, "dim": DIM, "heads": heads, "metrics": metrics}
    Path(out).write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    # the pure-Python path must agree with scikit-learn
    tm = TextModel(out)
    sample = test_rows[:300]
    ref = scam.predict_proba(_matrix([r["text"] for r in sample]))[:, 1]
    drift = max(abs(tm.predict(r["text"])["scam_probability"] - float(v)) for r, v in zip(sample, ref))
    metrics["export_max_drift"] = round(drift, 5)
    assert drift < 0.01, f"exported text model disagrees with sklearn ({drift})"
    Path(out).write_text(json.dumps({**payload, "metrics": metrics}, separators=(",", ":"), ensure_ascii=False))
    print(json.dumps(metrics, indent=1))
    print(f"saved {out} ({Path(out).stat().st_size / 1e6:.1f} MB)")
    return metrics


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train the scam / pressure text model")
    ap.add_argument("--data", default=str(ROOT / "data" / "text"))
    ap.add_argument("--out", default=str(MODEL_PATH))
    a = ap.parse_args(argv)
    return train(a.data, a.out)


if __name__ == "__main__":
    main()
