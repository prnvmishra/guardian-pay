import os

# Tests never call real AI services; the AI tests turn on a local stand-in.
os.environ["GUARDIAN_AI"] = "off"
os.environ["GUARDIAN_LOCAL_GRANITE"] = "off"
os.environ.setdefault("GUARDIAN_SIM", "0")
