"""Loopback-only, best-effort event stream for browser voice feedback."""

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class LocalVoiceEvents:
    def __init__(self, port=8765):
        self.clients = set()
        self.lock = threading.Lock()
        self.current_session = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/events":
                    self.send_error(404)
                    return
                inbox = queue.Queue(maxsize=32)
                with owner.lock:
                    owner.clients.add(inbox)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    origin = self.headers.get("Origin")
                    if origin in ("http://localhost:5173", "http://127.0.0.1:5173",
                                  "http://localhost:5174", "http://127.0.0.1:5174"):
                        self.send_header("Access-Control-Allow-Origin", origin)
                        self.send_header("Vary", "Origin")
                    self.end_headers()
                    self.wfile.write(b": connected\n\n")
                    if owner.current_session:
                        self.wfile.write(b"data: " + json.dumps(owner.current_session).encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            payload = inbox.get(timeout=10)
                            self.wfile.write(b"data: " + payload + b"\n\n")
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with owner.lock:
                        owner.clients.discard(inbox)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def emit(self, event):
        published_at_ms = time.time_ns() // 1_000_000
        payload = json.dumps({**event, "publishedAtEpochMs": published_at_ms}, separators=(",", ":")).encode("utf-8")
        with self.lock:
            if event.get("type") == "start":
                self.current_session = event
            elif event.get("type") in ("complete", "stop", "reset"):
                self.current_session = None
            for inbox in self.clients:
                if inbox.full():
                    try:
                        inbox.get_nowait()
                    except queue.Empty:
                        pass
                inbox.put_nowait(payload)
        return published_at_ms

    def close(self):
        self.server.shutdown()
        self.server.server_close()
