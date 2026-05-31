import logging
import os
import threading
import time
import urllib.request
from dotenv import load_dotenv
from miio import MiotDevice
from http.server import BaseHTTPRequestHandler, HTTPServer

load_dotenv("/home/sodas/src/irrigation_hub/.env")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PLUG_IP             = os.getenv("XIAOMI_PLUG_IP")
PLUG_TOKEN          = os.getenv("XIAOMI_PLUG_TOKEN")
VM_URL              = os.getenv("VM_URL", "http://localhost:8428")
MAX_RUN_SECONDS     = 12 * 60
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


def _get_power_w() -> int | None:
    try:
        result = plug.get_property_by(11, 2)
        return int(result[0]["value"])
    except Exception as exc:
        log.warning(f"Failed to get plug power: {exc}")
        return None


def _vm_write_state(is_on: bool, power_w: int | None) -> None:
    """Write plug state + wattage to VictoriaMetrics for full visibility."""
    try:
        fields = [f"on={int(is_on)}"]
        if power_w is not None:
            fields.append(f"power_w={power_w}")
        line = f"smartplug {','.join(fields)} {int(time.time())}000000000"
        req = urllib.request.Request(
            f"{VM_URL}/write", data=line.encode(), method="POST"
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        log.warning(f"Failed to write plug state to VM: {exc}")


def _poll_state() -> None:
    global _timer, _state_since
    while True:
        is_on = None
        try:
            with _lock:
                is_on = _is_on()
                timer_running = _timer is not None

                if is_on and not timer_running:
                    log.warning("Manual override detected — starting safety cutoff")
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

        if is_on is not None:
            power = _get_power_w()
            _vm_write_state(is_on, power)

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
            power = _get_power_w()
            power_str = f" ({power}W)" if power is not None else " — power N/A"
            if is_on and (power is None or power < 50):
                power_str += " ⚠ ON but no load"
            self._ok(f"{state} for {_fmt_duration(elapsed)}{power_str}\n")
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
