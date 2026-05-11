"""
The receiver listens for ESP-NOW packets from sensor nodes and forwards
each packet as a newline-delimited JSON string over USB serial (UART0).
The Pi reads /dev/ttyUSB0 (or /dev/ttyACM0) and parses the JSON.

No deep sleep — this node stays on permanently, powered via USB-C from the Pi.
"""

import espnow
import network
import struct
import json
import sys
import ubinascii

# ── Packet format must match esp_sensor.py ───────────────────────────────────
PACKET_FMT  = '<BHfffI'
PACKET_SIZE = struct.calcsize(PACKET_FMT)   # 18 bytes

# ── Wire ID → descriptive name ────────────────────────────────────────────────
NODE_NAMES = {1: "water_flow", 2: "tank_level"}

# ── ESP-NOW channel — must match sensor nodes ─────────────────────────────────
WIFI_CHANNEL = 1


def setup_espnow() -> espnow.ESPNow:
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(channel=WIFI_CHANNEL)

    e = espnow.ESPNow()
    e.active(True)
    return e


def emit_json(node_name: str, value: int, voltage: float, temp: float, humidity: float, ticks: int, rssi: int):
    """Print a JSON record to stdout (USB serial → Pi)."""
    record = {
        "node_id":  node_name,
        "value":    value,
        "voltage":  voltage,
        "temp_c":   temp,
        "humidity": humidity,
        "ticks_ms": ticks,
        "rssi":     rssi,
    }
    # Data gets sent per line, not buffered
    sys.stdout.write(json.dumps(record) + "\n")


def run():
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    mac = ubinascii.hexlify(sta.config('mac'), ':').decode()
    sys.stdout.write(json.dumps({"event": "boot", "receiver_mac": mac}) + "\n")

    e = setup_espnow()
    sys.stdout.write(json.dumps({"event": "listening", "channel": WIFI_CHANNEL}) + "\n")

    while True:
        # irecv() blocks until a packet arrives
        host, msg = e.irecv()
        if msg is None:
            continue

        rssi = 0
        try:
            peer_info = e.get_peer(host)
            # peer_info tuple: (mac, lmk, channel, ifidx, encrypt)
            # RSSI not directly in peer_info on MicroPython; read from recv stats if available
        except Exception:
            pass

        if len(msg) != PACKET_SIZE:
            sys.stdout.write(
                json.dumps({
                    "event": "bad_packet",
                    "from":  ubinascii.hexlify(host, ':').decode(),
                    "len":   len(msg),
                }) + "\n"
            )
            continue

        wire_id, value, voltage, temp, humidity, ticks = struct.unpack(PACKET_FMT, msg)
        node_name = NODE_NAMES.get(wire_id, f"unknown_{wire_id}")
        emit_json(node_name, value, voltage, temp, humidity, ticks, rssi)


run()