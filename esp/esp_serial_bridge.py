"""
esp_serial_bridge.py  —  runs on Raspberry Pi (Sodas)
Sodas irrigation project

Reads newline-delimited JSON from the receiver ESP32 over serial,
interprets sensor values, and writes metrics to VictoriaMetrics.

Metrics written:
  esp_water_flow_pulses     (raw pulse count over 2s window)
  esp_water_flow_battery_v  (water_flow node battery voltage)
  esp_tank_level            (1=liquid present, 0=absent)
  esp_tank_level_battery_v  (tank_level node battery voltage)
  esp_greenhouse_temp_c     (greenhouse air temperature, from water_flow node Si7021)
  esp_greenhouse_humidity   (greenhouse air humidity %, from water_flow node Si7021)
Run as a systemd service: irrigation-esp-bridge.service
Serial device: /dev/ttyACM0 or /dev/ttyUSB0 (pass as --port argument)
"""

import argparse
import json
import logging
import os
import sys
import time

import httpx
import serial
from dotenv import load_dotenv

load_dotenv("/home/sodas/src/irrigation_hub/.env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

VM_URL     = os.getenv("VM_URL", "http://localhost:8428")
BAUD_RATE  = 115200


# ── VictoriaMetrics write ──────────────────────────────────────────────────────


def vm_write(lines: list[str]) -> None:
    try:
        resp = httpx.post(f"{VM_URL}/write", content="\n".join(lines), timeout=5)
        resp.raise_for_status()
    except Exception as exc:
        log.warning(f"VM write failed: {exc}")


# ── Packet handlers ────────────────────────────────────────────────────────────

def handle_packet(packet: dict) -> None:
    event = packet.get("event")

    if event == "boot":
        log.info(f"Receiver booted — MAC: {packet.get('receiver_mac')}")
        return

    if event == "listening":
        log.info(f"Receiver listening on channel {packet.get('channel')}")
        return

    if event == "bad_packet":
        log.warning(f"Bad packet from {packet.get('from')} len={packet.get('len')}")
        return

    # Sensor data packet
    node_id  = packet.get("node_id")
    value    = packet.get("value")
    voltage  = packet.get("voltage", 0.0)
    temp     = packet.get("temp_c", 0.0)
    humidity = packet.get("humidity", 0.0)

    if node_id is None or value is None:
        log.warning(f"Unrecognised packet: {packet}")
        return

    now = int(time.time())
    lines = []

    if node_id == "water_flow":
        # ZJ-S201: ~450 pulses/litre → flow_L_per_min = (pulses / window_s) * (60 / 450)
        # Raw pulses stored; derived flow rate can be computed in Grafana
        lines.append(f"esp_water_flow_pulses value={float(value)} {now}000000000")
        lines.append(f"esp_water_flow_battery_v value={voltage} {now}000000000")
        lines.append(f"esp_greenhouse_temp_c value={temp} {now}000000000")
        lines.append(f"esp_greenhouse_humidity value={humidity} {now}000000000")
        log.info(f"water_flow: {value} pulses  batt={voltage}V  temp={temp}C  hum={humidity}%")

    elif node_id == "tank_level":
        # 1 = water present (tank full), 0 = absent
        lines.append(f"esp_tank_level value={float(value)} {now}000000000")
        lines.append(f"esp_tank_level_battery_v value={voltage} {now}000000000")
        log.info(f"tank_level: {'FULL' if value else 'LOW'}  batt={voltage}V")

    else:
        log.warning(f"Unknown node_id={node_id!r}")
        return

    vm_write(lines)


# ── Main loop ──────────────────────────────────────────────────────────────────

def main(port: str):
    log.info(f"Opening serial port {port} at {BAUD_RATE} baud")
    while True:
        try:
            with serial.Serial(port, BAUD_RATE, timeout=5) as ser:
                log.info("Serial port open — waiting for packets")
                while True:
                    raw = ser.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        packet = json.loads(line)
                        handle_packet(packet)
                    except json.JSONDecodeError:
                        log.warning(f"Non-JSON line: {line!r}")
        except serial.SerialException as exc:
            log.error(f"Serial error: {exc} — retrying in 5s")
            time.sleep(5)
        except KeyboardInterrupt:
            log.info("Shutting down")
            sys.exit(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ESP-NOW → VictoriaMetrics serial bridge")
    parser.add_argument("--port", default="/dev/ttyACM0",
                        help="Serial device (default: /dev/ttyACM0)")
    args = parser.parse_args()
    main(args.port)