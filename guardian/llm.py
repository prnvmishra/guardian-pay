"""Generative AI access: IBM watsonx.ai (Granite) first, a local Ollama
Granite second, and a plain-Python safety net when neither is reachable.

Only the standard library is used, so this runs unchanged on LinuxONE.

Configuration comes from environment variables or a `.env` file in the
project root:

    WATSONX_APIKEY       IBM Cloud API key
    WATSONX_PROJECT_ID   watsonx.ai project id
    WATSONX_URL          e.g. https://us-south.ml.cloud.ibm.com
    WATSONX_CHAT_MODEL   optional, picked automatically otherwise
    WATSONX_VISION_MODEL optional, picked automatically otherwise
    OLLAMA_URL           optional, e.g. http://localhost:11434
    OLLAMA_MODEL         default granite3.3:8b
    OLLAMA_VISION_MODEL  default granite3.2-vision
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API_VERSION = "2025-02-27"
DEFAULT_IAM_URL = "https://iam.cloud.ibm.com/identity/token"

# preferred models, best first; the first one the account offers is used
CHAT_PREFERENCE = [
    "ibm/granite-4-h-small", "ibm/granite-3-3-8b-instruct", "ibm/granite-3-8b-instruct",
    "ibm/granite-3-2-8b-instruct", "meta-llama/llama-3-3-70b-instruct",
]
VISION_PREFERENCE = [
    "meta-llama/llama-3-2-11b-vision-instruct", "ibm/granite-vision-3-3-2b",
    "ibm/granite-vision-3-2-2b", "meta-llama/llama-4-maverick-17b-128e-instruct-fp8",
    "mistralai/mistral-medium-2505", "mistralai/pixtral-12b",
]


class AIError(Exception):
    """The model could not be reached or gave an unusable answer."""


def _load_env_file():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _http(url, data=None, headers=None, timeout=25, form=False):
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            hdrs["Content-Type"] = "application/json"
    hdrs.setdefault("Accept", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise AIError(f"HTTP {e.code} from {urllib.parse.urlparse(url).netloc}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIError(f"Could not reach {urllib.parse.urlparse(url).netloc}: {e}") from None
    except json.JSONDecodeError:
        raise AIError("The AI service sent a response that is not JSON") from None


class Watsonx:
    name = "watsonx"

    def __init__(self, apikey, project_id, url):
        self.apikey = apikey
        self.project_id = project_id
        self.url = url.rstrip("/")
        self._token = None
        self._token_until = 0
        self._lock = threading.Lock()
        self.chat_model = os.environ.get("WATSONX_CHAT_MODEL") or None
        self.vision_model = os.environ.get("WATSONX_VISION_MODEL") or None
        self._discovered = False
        self._discover_lock = threading.Lock()

    def token(self):
        with self._lock:
            if self._token and time.time() < self._token_until:
                return self._token
            res = _http(os.environ.get("WATSONX_IAM_URL", DEFAULT_IAM_URL), {"grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                                  "apikey": self.apikey}, form=True, timeout=15)
            if "access_token" not in res:
                raise AIError("IBM Cloud did not return a token; check WATSONX_APIKEY")
            self._token = res["access_token"]
            self._token_until = time.time() + int(res.get("expires_in", 3600)) - 300
            return self._token

    def discover(self):
        """Ask watsonx which chat models this project can use. Safe to call
        from several threads: the first caller does the work, others wait."""
        with self._discover_lock:
            if self._discovered:
                return
            self._discover()
            self._discovered = True

    def _discover(self):
        try:
            q = urllib.parse.urlencode({"version": API_VERSION, "limit": 200,
                                        "filters": "function_text_chat,!lifecycle_withdrawn:and"})
            res = _http(f"{self.url}/ml/v1/foundation_model_specs?{q}", timeout=15)
            available = {m.get("model_id"): m for m in res.get("resources", [])}
        except AIError:
            available = {}
        if not available:
            self.chat_model = self.chat_model or CHAT_PREFERENCE[1]
            return
        if not self.chat_model:
            self.chat_model = next((m for m in CHAT_PREFERENCE if m in available), None) or next(
                (m for m in available if m.startswith("ibm/granite") and "vision" not in m and "guardian" not in m),
                next(iter(available)))
        if not self.vision_model:
            def can_see(spec):
                funcs = {f.get("id") for f in spec.get("functions", [])}
                return "image_chat" in funcs or "vision" in spec.get("model_id", "")
            self.vision_model = next((m for m in VISION_PREFERENCE if m in available), None) or next(
                (mid for mid, spec in available.items() if can_see(spec)), None)

    def chat(self, messages, max_tokens=400, temperature=0.2, vision=False):
        self.discover()
        model = self.vision_model if vision else self.chat_model
        if not model:
            raise AIError("No image-reading model is available in this watsonx project")
        payload = {
            "model_id": model,
            "project_id": self.project_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "time_limit": 20000,
        }
        url = f"{self.url}/ml/v1/text/chat?version={API_VERSION}"
        last = None
        for attempt in range(2):
            try:
                res = _http(url, payload, {"Authorization": f"Bearer {self.token()}"}, timeout=30)
                return res["choices"][0]["message"]["content"], model
            except (KeyError, IndexError):
                raise AIError("watsonx answered without a message") from None
            except AIError as e:
                last = e
                retry = any(code in str(e) for code in ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"))
                if "HTTP 401" in str(e):
                    self._token = None
                    retry = True
                if not retry or attempt:
                    break
                time.sleep(1.2)
        raise last


class Ollama:
    name = "ollama"

    def __init__(self, url):
        self.url = url.rstrip("/")
        self.chat_model = os.environ.get("OLLAMA_MODEL", "granite3.3:8b")
        self.vision_model = os.environ.get("OLLAMA_VISION_MODEL", "granite3.2-vision")

    def chat(self, messages, max_tokens=400, temperature=0.2, vision=False):
        converted = []
        for m in messages:
            if isinstance(m["content"], list):
                text = " ".join(p.get("text", "") for p in m["content"] if p.get("type") == "text")
                images = [p["image_url"]["url"].split(",", 1)[-1]
                          for p in m["content"] if p.get("type") == "image_url"]
                converted.append({"role": m["role"], "content": text, "images": images})
            else:
                converted.append(m)
        model = self.vision_model if vision else self.chat_model
        res = _http(f"{self.url}/api/chat", {
            "model": model, "messages": converted, "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }, timeout=90)
        try:
            return res["message"]["content"], model
        except KeyError:
            raise AIError("Ollama answered without a message") from None


def find_granite_gguf():
    """Locate the Granite GGUF model that ships in IBM's LinuxONE Jupyter
    image (used by its loan-approval lab), or one named in GRANITE_GGUF."""
    explicit = os.environ.get("GRANITE_GGUF")
    if explicit and Path(explicit).exists():
        return explicit
    try:
        from loan_demo.rag_utils import MODEL_PATH  # IBM's lab package in the image
        if MODEL_PATH and Path(MODEL_PATH).exists():
            return str(MODEL_PATH)
    except Exception:
        pass
    import glob
    for pattern in ("/home/ibm-user/**/*granite*.gguf", "/opt/**/*granite*.gguf",
                    str(Path.home() / "**/*granite*.gguf"), str(ROOT / "models/*.gguf")):
        hits = sorted(glob.glob(pattern, recursive=True))
        if hits:
            return hits[0]
    return None


class LlamaCpp:
    """IBM Granite running inside this process with llama.cpp. In IBM's
    LinuxONE image llama.cpp is built with zDNN, so matrix maths runs on the
    Telum chip's on-chip AI accelerator. No data leaves the machine."""

    name = "granite-local"

    def __init__(self, path):
        self.path = path
        self.chat_model = Path(path).name
        self.vision_model = None
        self._llm = None
        self._lock = threading.Lock()
        self.deadline = float(os.environ.get("GRANITE_DEADLINE_SECONDS", "25"))

    def _load(self):
        if self._llm is None:
            from llama_cpp import Llama
            self._llm = Llama(model_path=self.path, n_ctx=int(os.environ.get("GRANITE_CTX", "4096")),
                              n_threads=os.cpu_count() or 2, verbose=False)
        return self._llm

    def chat(self, messages, max_tokens=400, temperature=0.2, vision=False, json_mode=False):
        if vision:
            raise AIError("The on-device Granite model reads text only")
        flat = []
        for m in messages:
            content = m["content"]
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
            flat.append({"role": m["role"], "content": content})
        with self._lock:
            try:
                llm = self._load()
            except Exception as e:
                raise AIError(f"Could not load Granite: {e}") from None
            kwargs = {"messages": flat, "max_tokens": max_tokens, "temperature": temperature, "stream": True}
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            start = time.time()
            parts = []
            try:
                for chunk in llm.create_chat_completion(**kwargs):
                    delta = chunk["choices"][0].get("delta", {})
                    parts.append(delta.get("content") or "")
                    if time.time() - start > self.deadline:
                        raise AIError(f"Granite took longer than {self.deadline:.0f}s")
            except AIError:
                raise
            except Exception as e:
                raise AIError(f"Granite failed: {e}") from None
        return "".join(parts), self.chat_model


class AI:
    """Front door used by the rest of the app."""

    def __init__(self):
        _load_env_file()
        self.providers = []
        self.last_error = None
        if os.environ.get("GUARDIAN_AI", "on") == "off":
            return
        key, project, url = (os.environ.get("WATSONX_APIKEY"), os.environ.get("WATSONX_PROJECT_ID"),
                             os.environ.get("WATSONX_URL"))
        local = None
        if os.environ.get("GUARDIAN_LOCAL_GRANITE", "on") != "off":
            gguf = find_granite_gguf()
            if gguf:
                try:
                    import llama_cpp  # noqa: F401
                    local = LlamaCpp(gguf)
                except ImportError:
                    local = None
        # watsonx answers in 2-3 s with a larger Granite; the Granite inside
        # this machine is the backup and keeps working with no network.
        # GUARDIAN_PREFER_LOCAL=1 puts the on-machine model first (no data
        # leaves LinuxONE at all).
        cloud = Watsonx(key, project, url) if (key and project and url) else None
        order = [local, cloud] if os.environ.get("GUARDIAN_PREFER_LOCAL") == "1" else [cloud, local]
        self.providers.extend(p for p in order if p)
        if os.environ.get("OLLAMA_URL"):
            self.providers.append(Ollama(os.environ["OLLAMA_URL"]))

    @property
    def enabled(self):
        return bool(self.providers)

    def status(self):
        if not self.providers:
            return {"provider": "offline", "ready": False,
                    "note": "No AI service configured. Built-in safety checks are running instead."}
        p = self.providers[0]
        return {"provider": p.name, "ready": self.last_error is None,
                "backups": [x.name for x in self.providers[1:]],
                "chat_model": getattr(p, "chat_model", None), "vision_model": getattr(p, "vision_model", None),
                "error": self.last_error}

    def chat(self, messages, max_tokens=400, temperature=0.2, vision=False, json_mode=False):
        """Returns (text, provider, model). Tries each provider in order."""
        if not self.providers:
            raise AIError("No AI service configured")
        errors = []
        for p in self.providers:
            try:
                if isinstance(p, LlamaCpp):
                    text, model = p.chat(messages, max_tokens=max_tokens, temperature=temperature,
                                         vision=vision, json_mode=json_mode)
                else:
                    text, model = p.chat(messages, max_tokens=max_tokens, temperature=temperature, vision=vision)
                self.last_error = None
                return text, p.name, model
            except AIError as e:
                errors.append(f"{p.name}: {e}")
        self.last_error = "; ".join(errors)
        raise AIError(self.last_error)

    def chat_json(self, messages, required, max_tokens=500, vision=False):
        """Ask for JSON and make sure the required keys are there. One
        repair attempt if the first answer is not valid JSON."""
        text, provider, model = self.chat(messages, max_tokens=max_tokens, temperature=0.1, vision=vision,
                                          json_mode=True)
        data = parse_json(text)
        if data is None or not all(k in data for k in required):
            repair = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": "Reply again with only the JSON object, no other text. "
                                            f"It must contain the keys: {', '.join(required)}."},
            ]
            text, provider, model = self.chat(repair, max_tokens=max_tokens, temperature=0.0, vision=vision,
                                              json_mode=True)
            data = parse_json(text)
        if data is None or not all(k in data for k in required):
            raise AIError("The model did not return the expected JSON")
        return data, provider, model


def parse_json(text):
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    out = json.loads(text[start:i + 1])
                    return out if isinstance(out, dict) else None
                except json.JSONDecodeError:
                    return None
    return None
