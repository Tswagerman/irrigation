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
from machine import SoftI2C

# --# -- Configuration -------------------------------------------------------------
NODE_ID = "water_flow"

_NODE_WIRE_ID = {"water_flow": 1, "tank_level": 2}

RECEIVER_MAC = b'\xac\xa7\x04\xa0\x7c\xc0'

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
# f  = temp_c       (4 bytes float,  celsius, 0.0 for tank_level)
# f  = humidity     (4 bytes float,  percent, 0.0 for tank_level)
# I  = timestamp    (4 bytes uint32, ms since boot - relative ordering only)
PACKET_FMT = '<BHfffI'

def read_si7021():
    try:
        i2c = SoftI2C(sda=machine.Pin(6), scl=machine.Pin(7))
        i2c.writeto(0x40, b'\xF3')
        time.sleep_ms(20)
        raw_temp = struct.unpack('>H', i2c.readfrom(0x40, 2))[0]
        temp = ((175.72 * raw_temp) / 65536) - 46.85
        i2c.writeto(0x40, b'\xF5')
        time.sleep_ms(20)
        raw_hum = struct.unpack('>H', i2c.readfrom(0x40, 2))[0]
        humidity = ((125.0 * raw_hum) / 65536) - 6
        return round(temp, 2), round(humidity, 2)
    except Exception as ex:
        print("Si7021 read failed: " + str(ex))
        return 0.0, 0.0

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


def send_espnow(node_id, value, voltage, temp=0.0, humidity=0.0):
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
    payload = struct.pack(PACKET_FMT, wire_id, value, voltage, temp, humidity, time.ticks_ms())
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
        temp, humidity = read_si7021()
        print(f"[{NODE_ID}] pulses={value}  batt={voltage}V  temp={temp}C  hum={humidity}%")
        
    elif NODE_ID == "tank_level":
        sig_pin = machine.Pin(PIN_LEVEL_SIGNAL, machine.Pin.IN)
        value = read_level(sig_pin)
        temp, humidity = 0.0, 0.0
        print(f"[{NODE_ID}] level={value}  batt={voltage}V")

    else:
        print(f"Unknown NODE_ID={NODE_ID}")
        value = 0

    # Power down sensors to save battery
    pwr.value(0)

    ok = send_espnow(NODE_ID, value, voltage, temp, humidity)
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