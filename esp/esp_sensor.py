"""
Wiring (per node):
  GPIO 1  = flow sensor signal (ZJ-S201 green wire)   [NODE_ID="water_flow" only]
  GPIO 2  = liquid level signal (DFRobot)              [NODE_ID="tank_level" only]
  GPIO 3  = switched sensor power (HIGH = sensors on)
  5V rail = TP4056 OUT+
  GND     = shared via Wago

NODE_ID must be set to "water_flow" or "tank_level" before flashing.
"""

import time
import struct
import machine
import espnow
import network
import ubinascii

# --# -- Configuration -------------------------------------------------------------
NODE_ID = "tank_level"

_NODE_WIRE_ID = {"water_flow": 1, "tank_level": 2}

RECEIVER_MAC = b'\xac\xa7\x04\xb4\x4f\x34'

DEEP_SLEEP_MS   = 60_000          # 60 s between readings
SENSOR_WARMUP_MS = 500            # ms to wait after powering sensor before reading
FLOW_PULSE_WINDOW_MS = 2_000      # ms to count flow pulses (longer = more accurate)

# GPIO pins
PIN_SENSOR_POWER = 3              # HIGH to power sensors
PIN_FLOW_SIGNAL  = 1              # flow sensor pulse input
PIN_LEVEL_SIGNAL = 2              # liquid level digital input

# ---- Packet format (10 bytes) ------------------------------------------------------------------------------------------------------
# B  = wire_node_id (1 byte  uint8: 1=water_flow, 2=tank_level)
# H  = value_int    (2 bytes uint16: flow=pulse count, tank=0/1)
# f  = voltage      (4 bytes float,  battery voltage in V)
# I  = timestamp    (4 bytes uint32, ms since boot -relative ordering only)
PACKET_FMT = '<BHfI'


def read_battery_voltage():
    # Returns battery voltage via ADC on GPIO 4. Needs 100k/100k divider.
    # Returns 0.0 if divider not wired.
    try:
        adc = machine.ADC(machine.Pin(4), atten=machine.ADC.ATTN_11DB)
        volts = adc.read_uv() / 1_000_000 * 2  # read_uv uses factory calibration, x2 for divider
        return round(volts, 2)
    except Exception:
        return 0.0


def read_flow_pulses(pin, window_ms):
    # Count pulses from flow sensor over window_ms milliseconds.
    count = 0

    def _irq(_):
        nonlocal count
        count += 1

    pin.irq(trigger=machine.Pin.IRQ_RISING, handler=_irq)
    time.sleep_ms(window_ms)
    pin.irq(handler=None)
    return count


def read_level(pin):
    """Read DFRobot non-contact level sensor. Returns 1 (liquid present) or 0."""
    return pin.value()


def send_espnow(node_id, value, voltage):
    """Broadcast a sensor packet via ESP-NOW. Returns True on success."""
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.config(channel=1)

    e = espnow.ESPNow()
    e.active(True)

    try:
        e.add_peer(RECEIVER_MAC)
    except Exception:
        pass  

    wire_id = _NODE_WIRE_ID[node_id]
    payload = struct.pack(PACKET_FMT, wire_id, value, voltage, time.ticks_ms())
    try:
        e.send(RECEIVER_MAC, payload)
        return True
    except Exception as ex:
        print(f"ESP-NOW send failed: {ex}")
        return False
    finally:
        e.active(False)
        sta.active(False)


def run():
    pwr = machine.Pin(PIN_SENSOR_POWER, machine.Pin.OUT)
    pwr.value(1)
    time.sleep_ms(SENSOR_WARMUP_MS)

    voltage = read_battery_voltage()

    if NODE_ID == "water_flow":
        sig_pin = machine.Pin(PIN_FLOW_SIGNAL, machine.Pin.IN, machine.Pin.PULL_UP)
        value = read_flow_pulses(sig_pin, FLOW_PULSE_WINDOW_MS)
        print(f"[{NODE_ID}] pulses={value}  batt={voltage}V")

    elif NODE_ID == "tank_level":
        sig_pin = machine.Pin(PIN_LEVEL_SIGNAL, machine.Pin.IN)
        value = read_level(sig_pin)
        print(f"[{NODE_ID}] level={value}  batt={voltage}V")

    else:
        print(f"Unknown NODE_ID={NODE_ID}")
        value = 0

    # Power down sensors to save battery
    pwr.value(0)

    ok = send_espnow(NODE_ID, value, voltage)
    result = 'OK' if ok else 'FAILED'
    print("Send " + result)

    # Deep sleep
    print(f"Sleeping {DEEP_SLEEP_MS}ms")
    machine.deepsleep(DEEP_SLEEP_MS)


# ---- Entry point -------------------------------------------------------------------------------------------------------------------------------
sta = network.WLAN(network.STA_IF)
sta.active(True)
mac = ubinascii.hexlify(sta.config('mac'), ':').decode()
print("This node MAC: " + mac)
sta.active(False)

run()