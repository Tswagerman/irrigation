# Irrigation system — cheatsheet

## Alias for get_status.py
| Check status | `status` |

## Valve control
| Action | Command |
|--------|---------|
| Open valve | `curl -X POST http://localhost:8001/open` |
| Close valve | `curl -X POST http://localhost:8001/close` |
| Valve status | `curl http://localhost:8001/status` |

## Services
| Action | Command |
|--------|---------|
| Status all | `sudo systemctl list-units --type=service --state=running` |
| Restart logic | `sudo systemctl restart irrigation-logic` |
| Restart collector | `sudo systemctl restart irrigation-collector` |
| Watch collector logs | `sudo journalctl -f -u irrigation-collector` |
| Watch logic logs | `sudo journalctl -f -u irrigation-logic` |

## Config — control_logic.py
**Edit:** `sudo nano ~/src/irrigation_hub/control_logic.py`  
**Apply changes:** `sudo systemctl restart irrigation-logic`

| Setting | Variable | Current value |
|---------|----------|---------------|
| Start watering (primary) | `MOISTURE_THRESHOLD_LOW_PRIMARY` | `35.0` |
| Start watering (secondary) | `MOISTURE_THRESHOLD_LOW_SECONDARY` | `25.0` |
| Stop watering | `MOISTURE_THRESHOLD_HIGH` | `50.0` |
| Max duration | `MAX_WATER_DURATION` | `60 * 60` (1 hour) |

## Ecowitt gateway settings
| Setting | Value |
|---------|-------|
| Port | `8000` |
| Path | `/data/report/` |
| Protocol | Ecowitt |
| Interval | 60 seconds |

## Tailscale
| Action | Command |
|--------|---------|
| Check status | `tailscale status` |
