#!/home/sodas/venv/irrigation_hub/bin/python
import httpx
import sys
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8428"
VM_URL   = BASE_URL

# Fallback thresholds — used only if VictoriaMetrics has no stored values
MOISTURE_LOW_DEFAULT  = 50.0
MOISTURE_HIGH_DEFAULT = 60.0


def q(promql: str) -> float | None:
    try:
        resp = httpx.get(f"{VM_URL}/api/v1/query", params={"query": promql}, timeout=10)
        resp.raise_for_status()
        result = resp.json().get("data", {}).get("result", [])
        if result:
            return float(result[0]["value"][1])
    except Exception:
        return None
    return None


def fmt(val, suffix="", decimals=1, na="N/A"):
    if val is None:
        return na
    return f"{round(val, decimals)}{suffix}"


def bar(pct, width=20, fill="█", empty="░"):
    if pct is None:
        return "N/A"
    filled = int((pct / 100) * width)
    return fill * filled + empty * (width - filled)


def valve_is_open() -> bool:
    try:
        resp = httpx.get("http://localhost:8001/status", timeout=5)
        return resp.text.strip() == "open"
    except Exception:
        return False


def pump_status_str() -> str:
    try:
        resp = httpx.get("http://localhost:8003/status", timeout=5)
        return resp.text.strip()
    except Exception:
        return None


def moisture_status(moisture, threshold_low, threshold_high):
    if moisture is None:
        return "❓ No data"
    if moisture >= threshold_high:
        return "💧 Saturated"
    if moisture >= threshold_low:
        return "✅ OK"
    return "🌵 Dry — needs water"


def rain_status(rain_now, rain_prob, rain_int):
    parts = []
    if rain_now and rain_now > 0.01:
        parts.append(f"🌧  Raining now ({rain_now:.2f} mm/h)")
    if rain_prob and rain_prob > 0.3:
        parts.append(f"⛅ {int(rain_prob*100)}% chance of rain in next 12h")
    if rain_int and rain_int > 0.1:
        parts.append(f"🌂 Forecast intensity: {rain_int:.2f} mm/h")
    return parts if parts else ["☀️  No rain expected"]


def main():
    now = datetime.now()
    hour = now.hour

    print()
    print("=" * 52)
    print("  🌱  IRRIGATION SYSTEM STATUS")
    print(f"  {now.strftime('%A %d %b %Y  %H:%M:%S')}")
    print("=" * 52)

    threshold_low  = q("last_over_time(irrigation_logic_threshold[1h])")
    threshold_high = q("last_over_time(irrigation_logic_threshold_high[1h])")
    threshold_low  = threshold_low  if threshold_low  is not None else MOISTURE_LOW_DEFAULT
    threshold_high = threshold_high if threshold_high is not None else MOISTURE_HIGH_DEFAULT

    s1 = q("avg_over_time(ecowitt_sensors_soil_moisture_1[5m])")
    s2 = q("avg_over_time(ecowitt_sensors_soil_moisture_2[5m])")
    available = [v for v in [s1, s2] if v is not None]
    avg = round(sum(available) / len(available), 1) if available else None

    print()
    print("  🌍 SOIL MOISTURE")
    print(f"  Sensor 1 : {fmt(s1, '%', 1):>7}  {bar(s1)}")
    print(f"  Sensor 2 : {fmt(s2, '%', 1):>7}  {bar(s2)}")
    print(f"  Average  : {fmt(avg, '%', 1):>7}  {bar(avg)}")
    print(f"  Status   : {moisture_status(avg, threshold_low, threshold_high)}")
    print(f"  Threshold: {threshold_low}% (high: {threshold_high}%)")

    b1 = q("last_over_time(ecowitt_sensors_soil_battery_1[1h])")
    b2 = q("last_over_time(ecowitt_sensors_soil_battery_2[1h])")
    print()
    print("  🔋 SENSOR BATTERIES")
    print(f"  Sensor 1 : {fmt(b1, 'V', 2)}")
    print(f"  Sensor 2 : {fmt(b2, 'V', 2)}")

    temp      = q("last_over_time(weather_current_temp_c[2h])")
    humidity  = q("last_over_time(weather_current_humidity[2h])")
    wind      = q("last_over_time(weather_current_wind_speed[2h])")
    uv        = q("last_over_time(weather_current_uv_index[2h])")
    cloud     = q("last_over_time(weather_current_cloud_cover[2h])")
    rain_now  = q("last_over_time(weather_current_precip_intensity[2h])")
    rain_prob = q("max_over_time(weather_forecast_precip_probability[12h])")
    rain_int  = q("max_over_time(weather_forecast_precip_intensity[12h])")

    print()
    print("  🌤  WEATHER")
    print(f"  Temp     : {fmt(temp, '°C')}")
    print(f"  Humidity : {fmt(humidity, '%', 0)}")
    print(f"  Wind     : {fmt(wind, ' km/h')}")
    print(f"  UV Index : {fmt(uv, '', 0)}")
    print(f"  Cloud    : {fmt(cloud, '%', 0)}")
    for line in rain_status(rain_now, rain_prob, rain_int):
        print(f"  {line}")

    last_action    = q("last_over_time(irrigation_logic_action[10m])")
    last_triggered = q("last_over_time(irrigation_logic_triggered[10m])")
    last_duration  = q("last_over_time(irrigation_logic_session_duration_s[10m])")
    reason_moisture_high = q("last_over_time(irrigation_logic_reason_moisture_high[10m])")
    reason_max_dur       = q("last_over_time(irrigation_logic_reason_max_duration[10m])")

    print()
    print("  💧 IRRIGATION")

    valve_open = valve_is_open()
    is_watering = last_action == 1.0 or valve_open
    if valve_open and last_action != 1.0:
        print("  Note     : ⚠️  Valve open manually (not via control logic)")
    if is_watering:
        dur = int(last_duration) if last_duration else 0
        print(f"  Status   : 🚿 WATERING  ({timedelta(seconds=dur)} elapsed)")
    else:
        print(f"  Status   : ⏸  Idle")

    reasons = []
    if avg is not None and avg >= threshold_low and not is_watering:
        reasons.append(f"moisture OK ({avg}% ≥ {threshold_low}%)")
    if avg is not None and avg < threshold_low and not is_watering:
        reasons.append(f"moisture low ({avg}% < {threshold_low}%) but outside window")
    if reason_moisture_high == 1:
        reasons.append("stopped — moisture reached target")
    if reason_max_dur == 1:
        reasons.append("stopped — max duration reached")

    if reasons:
        print(f"  Reason   : {', '.join(reasons)}")

    in_window = any(s <= hour < e for s, e in [(6, 10), (14, 21)])
    print(f"  Window   : {'✅ Active' if in_window else '⏰ Outside window'} (hour={hour})")
    last_pump_action     = q("last_over_time(irrigation_logic_pump_action[2h])")
    last_pump_reason     = q("last_over_time(irrigation_logic_pump_reason[2h])")
    pump_warning         = q("last_over_time(irrigation_logic_pump_warning[2h])")
    emergency_topups     = q("last_over_time(irrigation_logic_emergency_topups_today[2h])")
    plug_status          = pump_status_str()

    print()
    print("  🔌 PUMP")

    if plug_status is not None:
        is_pump_on = plug_status.startswith("on")
        icon = "⚡ ON" if is_pump_on else "⏸  Off"
        print(f"  Status   : {icon}  ({plug_status})")
    else:
        print(f"  Status   : ❓ Driver unreachable")

    if last_pump_reason is not None:
        reason_label = "scheduled" if last_pump_reason == 0.0 else "emergency top-up"
        action_label = "ran" if last_pump_action == 1.0 else "skipped"
        print(f"  Last run : {action_label} ({reason_label})")
    elif is_pump_on:
        icon = "⚠️"
        print(f"  {icon} Driver turned on manually, control logic bypassed")

    if emergency_topups is not None and emergency_topups > 0:
        icon = "⚠️ " if emergency_topups >= 2 else "ℹ️ "
        print(f"  Emerg.   : {icon} {int(emergency_topups)} top-up(s) today")

    if pump_warning == 1.0:
        print(f"  WARNING  : 🚨 Max emergency top-ups reached — check pump/tank!")

    print()
    print("=" * 52)
    print()


if __name__ == "__main__":
    main() 
