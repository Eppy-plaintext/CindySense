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
        self.available = threading.Condition(self.lock)
        self.current_session = None
        self.current_session_delivered = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/events":
                    self.send_error(404)
                    return
                inbox = queue.Queue(maxsize=32)
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
                    self.wfile.flush()
                    while True:
                        with owner.available:
                            claimed, session = owner.claim(inbox)
                            if claimed:
                                break
                            owner.available.wait(timeout=2)
                        self.wfile.write(b": waiting\n\n")
                        self.wfile.flush()
                    self.wfile.write(b"event: speaker\ndata: active\n\n")
                    if session:
                        self.wfile.write(b"data: " + json.dumps(session).encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            payload = inbox.get(timeout=2)
                            self.wfile.write(b"data: " + payload + b"\n\n")
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with owner.available:
                        owner.clients.discard(inbox)
                        owner.available.notify()

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def claim(self, inbox):
        """Called with the availability lock held; only one browser may speak."""
        if self.clients:
            return False, None
        self.clients.add(inbox)
        if not self.current_session:
            return True, None
        session = {**self.current_session, "replay": self.current_session_delivered}
        self.current_session_delivered = True
        return True, session

    def emit(self, event):
        published_at_ms = time.time_ns() // 1_000_000
        payload = json.dumps({**event, "publishedAtEpochMs": published_at_ms}, separators=(",", ":")).encode("utf-8")
        with self.lock:
            if event.get("type") == "start":
                self.current_session = event
                self.current_session_delivered = bool(self.clients)
            elif event.get("type") in ("complete", "stop", "reset"):
                self.current_session = None
                self.current_session_delivered = False
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
