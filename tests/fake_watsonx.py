"""A stand-in for IBM Cloud IAM and watsonx.ai used by the tests.

It checks that requests have the shape the real service expects (form-encoded
API key exchange, Bearer token, version parameter, model_id, project_id,
messages) and answers like a chat model would.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class State:
    def __init__(self):
        self.requests = []
        self.mode = "ok"          # ok | garbage_once | down
        self._garbage_sent = False
        self.saathi_reply = {"reply": "क्या कोई आपसे अभी कॉल पर पैसे भेजने को कह रहा है?",
                             "risk": "unclear", "signals": [], "done": False}


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            state.requests.append(("GET", url.path, q, None, dict(self.headers)))
            if url.path == "/ml/v1/foundation_model_specs":
                assert "version" in q and "function_text_chat" in q["filters"][0]
                return self._send(200, {"resources": [
                    {"model_id": "ibm/granite-3-3-8b-instruct", "functions": [{"id": "text_chat"}]},
                    {"model_id": "meta-llama/llama-3-2-11b-vision-instruct",
                     "functions": [{"id": "text_chat"}, {"id": "image_chat"}]},
                ]})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            q = parse_qs(url.query)
            if url.path == "/identity/token":
                form = parse_qs(raw)
                state.requests.append(("POST", url.path, q, form, dict(self.headers)))
                assert self.headers["Content-Type"] == "application/x-www-form-urlencoded"
                assert form["grant_type"] == ["urn:ibm:params:oauth:grant-type:apikey"]
                if form["apikey"] != ["test-key"]:
                    return self._send(400, {"errorMessage": "Provided API key could not be found."})
                return self._send(200, {"access_token": "tok-123", "expires_in": 3600})

            body = json.loads(raw)
            state.requests.append(("POST", url.path, q, body, dict(self.headers)))
            if url.path != "/ml/v1/text/chat":
                return self._send(404, {"error": "not found"})
            assert self.headers["Authorization"] == "Bearer tok-123"
            assert "version" in q
            assert body["project_id"] == "proj-1" and body["model_id"] and isinstance(body["messages"], list)
            if state.mode == "down":
                return self._send(503, {"errors": [{"message": "service unavailable"}]})
            if state.mode == "garbage_once" and not state._garbage_sent:
                state._garbage_sent = True
                return self._send(200, {"choices": [{"message": {"content": "Sure! Here is my answer."}}]})

            system = " ".join(m["content"] for m in body["messages"]
                              if m["role"] == "system" and isinstance(m["content"], str))
            last = body["messages"][-1]["content"]
            last_text = last if isinstance(last, str) else " ".join(
                p.get("text", "") for p in last if p.get("type") == "text")
            if "Saathi" in system:
                answer = dict(state.saathi_reply)
                if "beti" in last_text or "बेटी" in last_text:
                    answer = {"reply": "अच्छा जी, धन्यवाद। मैं आपकी बेटी से पुष्टि करवा देता हूँ।",
                              "risk": "low", "signals": ["Paying own daughter"], "done": True}
            elif "fraud analyst" in system:
                has_image = isinstance(last, list) and any(p.get("type") == "image_url" for p in last)
                scam = "CBI" in last_text or has_image
                answer = {"verdict": "scam" if scam else "looks_safe",
                          "scam_type": "digital_arrest" if scam else "none",
                          "red_flags": ["Claims to be CBI", "Demands money to verify"] if scam else [],
                          "advice_en": "Do not pay." if scam else "Looks fine.",
                          "advice_hi": "पैसे मत भेजिए।" if scam else "ठीक लग रहा है।",
                          "accounts_found": ["50100447122906"] if scam else []}
            elif "case notes" in system:
                answer = {"summary": "Elderly customer pressured by a fake CBI caller.",
                          "likely_scam_type": "digital_arrest",
                          "recommended_action": "Keep the payment stopped and report the account to 1930.",
                          "confidence": "high"}
            else:
                answer = {"reply": "ok"}
            self._send(200, {"choices": [{"message": {"role": "assistant",
                                                      "content": "```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```"}}]})

    return Handler


def start():
    state = State()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, state
