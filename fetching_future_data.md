# Fetching Future Price Data — Notes & Open Questions

## What the API gives us

- **Endpoint**: `meter-data/unified-metrics/?metrics=pricing&resolution=hour&window_start=...&window_end=...`
- **Auth**: `Authorization: <api_key>` (bare token, no prefix)
- **Frame structure** (nested, not flat):
  ```json
  {
    "start": "2026-06-01T00:00:00Z",
    "end":   "2026-06-01T01:00:00Z",
    "metrics": {
      "pricing": {
        "tge_price":           0.5333,
        "price_gross":         0.8811,
        "price_prosumer_gross":0.6559,
        "is_cheap":  false,
        "is_expensive": false
      }
    }
  }
  ```
- **48h window**: 46 of 48 frames have `tge_price != null` (today + tomorrow after TGE publishes ~13:00 CET; D+2 frames are null)
- **TGE publishes D+1 prices**: ~13:00–14:30 CET each day

## What HA supports (and doesn't)

| Mechanism | Future timestamps? | Usable in automations? | Shown in sensor history? |
|---|---|---|---|
| Sensor state (state machine) | ❌ — recorded in real-time only | ✅ `states('sensor.x')` | ✅ click sensor → history |
| External statistics (`async_add_external_statistics`) | ✅ | ❌ not directly | ❌ only via Statistics Graph card |

Key constraint: **you cannot pre-populate a sensor's state history with future values**. The state machine only records what a sensor reports at the current moment.

## Approaches explored (and why abandoned)

### 1. External statistics (`async_add_external_statistics`)
Inject 46 hourly price points into HA's recorder with their real timestamps (including future hours). Visualised via a `statistics-graph` Lovelace card.

**Why abandoned**: The data is not accessible in automations or templates, and the visualisation requires a dedicated card — it doesn't feel like a "sensor" in the normal HA sense.

### 2. Peak/dip sensors
Three sensors: `peak_morning` (most expensive 06:00–13:00), `peak_evening` (most expensive 16:00–22:00), `dip_hour` (cheapest all-day). Each shows price as state and the hour as an attribute.

**Why abandoned**: Felt "stupid and unusable" — too much information lost (only 3 numbers, not the full curve).

## Open questions for next time

- What is the actual use case? Automation ("avoid peak hours"), display ("show me tomorrow's curve"), or both?
- If display: are we OK with a Lovelace card separate from sensor history?
- If automation: do we need the full array, or just key hours (next cheap slot, etc.)?
- Would a template sensor that reads from coordinator data (exposing the array as attributes) be acceptable, despite HA's warnings about large attributes?
- Could a custom Lovelace card reading from sensor attributes solve the display problem cleanly?
