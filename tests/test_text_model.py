from pathlib import Path

from guardian import ai_features
from guardian.text_model import TextModel, features, normalize, train

DATA = Path(__file__).resolve().parent.parent / "data" / "text"


def test_normalize_masks_numbers_links_and_money():
    t = normalize("Pay Rs 5,000 to 5010 0447 1229 06 at http://x.co now")
    assert "xmoneyx" in t and "xacctx" in t and "xurlx" in t


def test_features_are_normalised_and_handle_devanagari():
    f = features("पुलिस वाले वीडियो कॉल पर हैं")
    assert f and abs(sum(v * v for v in f.values()) - 1) < 1e-9
    assert features("") == {}


def test_train_export_and_predict(tmp_path):
    out = tmp_path / "text_model.json"
    metrics = train(DATA, out)
    assert metrics["scam_holdout"]["auc"] > 0.95
    assert metrics["export_max_drift"] < 0.01
    m = TextModel(out)
    spam = m.predict("WINNER!! You have won a 1000 cash prize. Call 09061701461 to claim now")
    ham = m.predict("Ok, I'll be home by 7. Do you want me to get milk?")
    assert spam["scam_probability"] > 0.8 > ham["scam_probability"]


def test_ml_risk_mapping():
    assert ai_features.ml_risk(None) == "unclear"
    assert ai_features.ml_risk({"scam_probability": 0.9}) == "high"
    assert ai_features.ml_risk({"scam_probability": 0.7, "urgency": {"label": "high"}}) == "high"
    assert ai_features.ml_risk({"scam_probability": 0.7}) == "medium"
    assert ai_features.ml_risk({"scam_probability": 0.1}) == "low"
