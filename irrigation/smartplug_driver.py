import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
from miio import MiotDevice

load_dotenv("/home/sodas/src/irrigation_hub/.env")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PLUG_IP             = os.getenv("XIAOMI_PLUG_IP")
PLUG_TOKEN          = os.getenv("XIAOMI_PLUG_TOKEN")
MAX_RUN_SECONDS     = 5 * 60
MANUAL_OVERRIDE_MAX = 10 * 60

plug = MiotDevice(PLUG_IP, PLUG_TOKEN)
_timer:       threading.Timer | None = None
_lock         = threading.Lock()
_state_since: float = time.time()


def _fmt_duration(seconds: int) -> str:
    if seconds >= 86400:
        return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def _is_on() -> bool:
    result = plug.get_property_by(2, 1)
    return result[0]["value"]


def _auto_off() -> None:
    global _timer, _state_since
    log.warning("Safety timer fired — turning plug OFF")
    with _lock:
        try:
            plug.set_property_by(2, 1, False)
        except Exception as exc:
            log.error(f"Failed to turn plug off during safety cutoff: {exc}")
        _timer = None
        _state_since = time.time()


def _poll_state() -> None:
    global _timer, _state_since
    while True:
        try:
            with _lock:
                is_on = _is_on()
                timer_running = _timer is not None

                if is_on and not timer_running:
                    log.warning("Manual override detected — starting 1 hour safety cutoff")
                    t = threading.Timer(MANUAL_OVERRIDE_MAX, _auto_off)
                    t.daemon = True
                    t.start()
                    _timer = t
                    _state_since = time.time()

                elif not is_on and timer_running:
                    log.info("Plug turned off manually — cancelling safety timer")
                    _timer.cancel()
                    _timer = None
                    _state_since = time.time()

        except Exception as exc:
            log.warning(f"Plug poll failed: {exc}")

        time.sleep(30)


class SmartPlugDriver:
    def on(self, duration: int = MAX_RUN_SECONDS) -> None:
        global _timer, _state_since
        safe_dur = min(duration, MAX_RUN_SECONDS)
        with _lock:
            if _timer:
                _timer.cancel()
            try:
                plug.set_property_by(2, 1, True)
            except Exception as exc:
                log.error(f"Failed to turn plug on: {exc}")
                return
            _state_since = time.time()
            _timer = threading.Timer(safe_dur, _auto_off)
            _timer.daemon = True
            _timer.start()
        log.info(f"Plug ON — safety cutoff in {safe_dur}s")

    def off(self) -> None:
        global _timer, _state_since
        with _lock:
            if _timer:
                _timer.cancel()
                _timer = None
            try:
                plug.set_property_by(2, 1, False)
            except Exception as exc:
                log.error(f"Failed to turn plug off: {exc}")
                return
            _state_since = time.time()
        log.info("Plug OFF")

    @property
    def is_on(self) -> bool:
        try:
            return _is_on()
        except Exception as exc:
            log.warning(f"Failed to get plug status: {exc}")
            return False


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        if   self.path == "/on":  _plug.on();  self._ok("on\n")
        elif self.path == "/off": _plug.off(); self._ok("off\n")
        else: self._respond(404, "unknown\n")

    def do_GET(self):
        if self.path == "/status":
            is_on = _plug.is_on
            elapsed = round(time.time() - _state_since)
            state = "on" if is_on else "off"
            self._ok(f"{state} for {_fmt_duration(elapsed)}\n")
        else:
            self._respond(404, "unknown")

    def _ok(self, body: str):
        self._respond(200, body)

    def _respond(self, code: int, body: str):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body.encode())


_plug = SmartPlugDriver()

if __name__ == "__main__":
    try:
        poll_thread = threading.Thread(target=_poll_state, daemon=True)
        poll_thread.start()
        server = HTTPServer(("127.0.0.1", 8003), _Handler)
        log.info("Smart plug driver listening on http://127.0.0.1:8003")
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        _plug.off()
