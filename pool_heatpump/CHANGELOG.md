# Changelog

## 1.3.1

- Fix an MQTT reconnect loop and the pump repeatedly re-registering:
  - Use a unique MQTT client id per run (a lingering broker session with the
    same id was kicking the connection in a loop).
  - Subscribe only to the command topics, not a wildcard that also received the
    add-on's own retained `module/target` message.
  - Query the module over UDP only at startup and after module button actions,
    not on every MQTT reconnect (which was disturbing the pump link).
  - Serialize all writes to the pump socket and handle MQTT commands off the
    reader thread, so frames can no longer interleave/corrupt.

## 1.3.0

- Climate now supports **cool / heat / auto / off**, matching the app. Mode
  register (2000) values verified against the app: cool=1, heat=2, auto=4.
  Selecting a mode in Home Assistant sets the mode register and powers the unit
  on; selecting "off" powers it off.

## 1.2.2

- Fix **Ambient temperature** staying "Unknown": the ambient block is pushed by
  the pump only ~once per minute and is not part of the poll dump. The bridge
  now publishes state as soon as any telemetry block arrives (throttled to every
  3 s), so ambient (and every value) appears as soon as it is received.

## 1.2.1

- Add a **Reboot module** button (sends `AT+Z` to the WiFi module over UDP) to
  force a reconnect without the command line.

## 1.2.0

- Proper sensors, verified against the manufacturer app:
  - **Inlet water temperature** (reg 1003) — now also the climate's current
    temperature, matching the app's main reading.
  - **Outlet water temperature** (reg 1001).
  - **Ambient temperature** (reg 307).
  - **Fault code** (reg 1004), decoded as letter+number, e.g. `P01` (no flow);
    `OK` when there is no fault.
- Removed the old "Pool secondary temperature" sensor.

## 1.1.3

- Resilient MQTT: the client now reconnects automatically after a dropped
  connection (previously a `BrokenPipeError` killed the state publisher). On
  every (re)connect it republishes discovery and re-subscribes.
- The state-publish loop no longer crashes on transient errors.
- Silence the benign `unit=0x81 fc=0x41` frame (the pump's reply to our poll).

## 1.1.2

- Robust startup: read the add-on options directly from `/data/options.json`
  instead of via bashio, so user-provided values work even if the Supervisor
  API is unavailable. The API is now only used to auto-discover the MQTT service
  when the MQTT host is left blank. Removes the bashio dependency in `run.sh`.

## 1.1.1

- Fix: add `hassio_api: true` so the add-on can read its options and the MQTT
  service from the Supervisor API. Without it, startup logged "Unable to access
  the API, forbidden" and MQTT failed with "Name does not resolve".

## 1.1.0

- Add **Adopt module (point to HA)** and **Restore module to cloud** buttons
  (via MQTT) so the WiFi module can be repointed from Home Assistant, no command
  line needed.
- Add a **Module target** sensor showing where the module currently points.
- New options: `module_ip` (pin the module when several Hi-Flying/HF modules
  exist on the LAN), `bridge_host`, `cloud_host`, `cloud_port`.
- Robust module selection: connected pump IP → `module_ip` → filtered discovery.
- Enable `host_network` so the add-on can reach the module over UDP 48899.
- Docs: MQTT fields inherit from the Supervisor MQTT service when left blank;
  local control and the manufacturer cloud/app are mutually exclusive.

## 1.0.0

- Initial release: local Modbus-TCP/MBAP bridge that replaces the manufacturer
  cloud for AquaTemp/fzdbiology-family pool heat pumps.
- Decodes pushed telemetry (`FC 0x10`) and exposes a `climate` entity plus a
  temperature sensor via MQTT auto-discovery.
- Sends commands (target temperature, power, mode) with `FC 0x06`.
- Zero runtime dependencies (Python stdlib + a minimal MQTT client).
