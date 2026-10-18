"""Download the public text datasets used to train the text model.

    python -m guardian.fetch_data

Sources (all public, licences on their pages):
    UCI SMS Spam Collection (CC BY 4.0) - shipped in data/text already
    Nithingayakwad/Indian_Cyber_Scam_PhoneCall_Hinglish_Dataset (Apache-2.0)
    anmolshrivastav/scam_ham_india_14_languages (MIT)
    Yash1072006/hinglish-scam-text-dataset (CC BY 4.0)
"""

import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "text"

DATASETS = [
    # (repo, picks: list of (substring in file path, extension, saved name))
    ("Nithingayakwad/Indian_Cyber_Scam_PhoneCall_Hinglish_Dataset", [("", ".csv", "hinglish_calls.csv")]),
    ("anmolshrivastav/scam_ham_india_14_languages", [("train", ".parquet", "india_14_train.parquet"),
                                                      ("test", ".parquet", "india_14_test.parquet")]),
    ("Yash1072006/hinglish-scam-text-dataset", [("", ".json", "hinglish_scam_text.json")]),
]


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "guardian-pay/1.0"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch(repo, picks):
    with _get(f"https://huggingface.co/api/datasets/{repo}") as r:
        files = [s["rfilename"] for s in json.load(r).get("siblings", [])]
    done = []
    for needle, ext, name in picks:
        match = next((f for f in files if f.endswith(ext) and needle in f.lower() and "readme" not in f.lower()),
                     None)
        if not match:
            print(f"  {repo}: no {ext} file matching '{needle}' (files: {', '.join(files[:8])})")
            continue
        dest = OUT / name
        with _get(f"https://huggingface.co/datasets/{repo}/resolve/main/{match}", timeout=300) as r, \
                open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        done.append(f"{name} ({dest.stat().st_size / 1e6:.1f} MB)")
    return done


def to_csv():
    """The text model reads CSV so it can train where pyarrow isn't available
    (IBM's s390x image). Convert the parquet splits once."""
    parts = [OUT / "india_14_train.parquet", OUT / "india_14_test.parquet"]
    if not all(p.exists() for p in parts):
        return
    import pandas as pd
    frames = [pd.read_parquet(p).assign(split=s) for p, s in zip(parts, ("train", "test"))]
    pd.concat(frames).to_csv(OUT / "india_14lang.csv", index=False)
    for p in parts:
        p.unlink()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ok = True
    for repo, picks in DATASETS:
        try:
            got = fetch(repo, picks)
            print(f"{repo}: {', '.join(got) or 'nothing saved'}")
        except Exception as e:  # report every source, keep going
            ok = False
            print(f"{repo}: failed ({e})")
    try:
        to_csv()
    except ImportError:
        print("pyarrow missing: kept the parquet files; install pyarrow to convert them")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
