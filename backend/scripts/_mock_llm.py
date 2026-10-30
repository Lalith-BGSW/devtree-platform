"""A local stand-in for the Azure-OpenAI-style chat-completions gateway, so the LLM code can be tested
without network access to the real one (which is internal to Bosch). Records every request it receives."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockLLM:
    def __init__(self):
        self.requests = []      # {"path", "headers" (lower-cased), "body"}
        self.script = []        # [(status, payload, extra_headers)] consumed one per request, then fall back to reply
        self.reply = None       # callable(body) -> assistant text
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
                outer.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                       "body": json.loads(raw) if raw else None})
                if outer.script:
                    status, payload, extra = outer.script.pop(0)
                else:
                    text = outer.reply(outer.requests[-1]["body"]) if outer.reply else "OK"
                    status, extra = 200, {}
                    payload = {"choices": [{"message": {"content": text}}],
                               "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST

            def log_message(self, *a):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self):
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_port}/api"      # same shape as the BMF base URL

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
