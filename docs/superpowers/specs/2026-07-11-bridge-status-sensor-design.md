# Bridge status sensor + availability (1.5.0)

Date: 2026-07-11
Status: approved

## Problem

On 2026-07-10 23:30 the pump stopped pushing telemetry and entered a
*registration storm*: it re-sent the 0x41 registration frame every 2 s for
11.5 h (~107k frames), ignoring the bridge's acks and polls. Home Assistant
kept showing the last received values (20.9 °C, 0 %, P01) as if current, with
no indication anything was wrong. Rebooting the WiFi module (AT+Z) did not
help; power-cycling the heat pump at the breaker did.

Two gaps:

1. No entity surfaces bridge-level (communication) problems, as opposed to
   pump faults (the existing Fault code sensor).
2. Telemetry entities keep stale values indefinitely instead of becoming
   unavailable.

## Design

### New sensor: Bridge status

MQTT discovery sensor, `entity_category: diagnostic` (like Module target).

- State: `ok` | `registration_storm` | `no_telemetry` | `pump_disconnected`
- Attributes via `json_attributes_topic`:
  - `detail` — human description of the problem
  - `action` — what the user should do (for `registration_storm`:
    "Power-cycle the heat pump at the breaker — rebooting the WiFi module
    does not help")
  - `since` — ISO timestamp when the condition started
  - `count` — number of registration frames seen (storm only)

### Detection

Pure function evaluated in the 30 s state loop and on relevant frame events:

- `registration_storm` — ≥ 10 consecutive 0x41 registration frames with no
  telemetry block (0x10) in between. Healthy behaviour is a single
  registration immediately followed by a full dump; 10 frames ≈ 20 s of
  storm. Also log a single warning when entering the state (not per frame).
- `no_telemetry` — pump TCP-connected but no telemetry block for > 5 min
  (normal cadence is a block every ~50 s).
- `pump_disconnected` — no pump TCP connection for > 2 min.
- Priority: `pump_disconnected` > `registration_storm` > `no_telemetry`.
- Any received telemetry block resets counters and returns the state to `ok`.

### Availability

- New retained topic `heatpump/<id>/availability` with `online`/`offline`.
- When status ≠ `ok`, publish `offline`; when back to `ok`, publish `online`.
- Entities that use it (become "unavailable" in HA when there is a problem):
  climate, inlet/outlet/ambient temperatures, compressor output rate,
  fault code.
- Entities that must NOT use it (stay visible to show the error and let the
  user act): Bridge status, Module target, the three module buttons.
- MQTT LWT on the same topic (`offline`): if the add-on process dies, all
  telemetry entities go unavailable. On connect the bridge publishes the
  current computed value.

### Out of scope

- No new add-on options; thresholds are constants in the bridge.
- No HA persistent notifications (users can automate on the sensor).
- No change to protocol handling, commands, or module actions.

## Verification

1. Local: run the bridge on the dev machine with a fake pump script that
   replays a registration storm / goes silent / disconnects; assert the
   status transitions and availability publishes (no real broker needed for
   the pure detection function; MQTT behaviour observed with the real one).
2. Live: deploy 1.5.0 to the user's HA, confirm the new sensor appears,
   state is `ok` with a healthy pump, and telemetry entities show
   "unavailable" while the add-on is stopped (LWT).

## Release

Version 1.5.0, CHANGELOG entry, dedicated version-bump commit.
