import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import RPi.GPIO as GPIO

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

RELAY_PIN        = 18
RELAY_ON         = GPIO.LOW   # active-low: LOW energizes relay → valve opens
RELAY_OFF        = GPIO.HIGH  # HIGH → relay off → valve closes
MAX_OPEN_SECONDS = 15 * 60

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(RELAY_PIN, GPIO.OUT, initial=RELAY_OFF)


class ValveDriver:
    def __init__(self):
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return GPIO.input(RELAY_PIN) == RELAY_ON

    def open_valve(self, duration_seconds: int = MAX_OPEN_SECONDS) -> None:
        safe_dur = min(int(duration_seconds), MAX_OPEN_SECONDS)
        with self._lock:
            self._cancel_timer()
            GPIO.output(RELAY_PIN, RELAY_ON)
            self._timer = threading.Timer(safe_dur, self._auto_close)
            self._timer.daemon = True
            self._timer.start()
        log.info(f"Valve OPEN — dead-man closes in {safe_dur}s")

    def close_valve(self) -> None:
        with self._lock:
            self._cancel_timer()
            GPIO.output(RELAY_PIN, RELAY_OFF)
        log.info("Valve CLOSED")

    def _auto_close(self) -> None:
        log.warning("Dead-man timer fired — closing valve automatically")
        with self._lock:
            GPIO.output(RELAY_PIN, RELAY_OFF)
            self._timer = None

    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        if   self.path == "/open":  _driver.open_valve();  self._ok("opened")
        elif self.path == "/close": _driver.close_valve(); self._ok("closed")
        else: self._respond(404, "unknown")

    def do_GET(self):
        if self.path == "/status":
            self._ok("open" if _driver.is_open else "closed")
        else:
            self._respond(404, "unknown")

    def _ok(self, body: str):
        self._respond(200, body)

    def _respond(self, code: int, body: str):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body.encode())


_driver = ValveDriver()

if __name__ == "__main__":
    try:
        server = HTTPServer(("127.0.0.1", 8001), _Handler)
        log.info("Valve driver listening on http://127.0.0.1:8001")
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        _driver.close_valve()
        GPIO.cleanup()