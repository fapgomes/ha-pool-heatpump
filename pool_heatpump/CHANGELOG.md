# Changelog

## 1.6.2

- Skip the post-block poll when the pump's boot dump already delivered the
  settings block (2000) — after a pump boot the poll landed mid-dump, which
  is exactly the collision window the 1.6.x fixes exist to avoid. The single
  poll now happens only when needed (bridge restart mid-session), 2 s after
  a lone pushed block with the bus quiet.

## 1.6.1

- Fix the 1.6.0 on-connect poll: after a power-on the module connects while
  the pump's comms processor is still booting, and a poll during boot wedges
  it (registration storm) just like a mid-session one. The single poll now
  goes out 2 s after the FIRST telemetry block of each connection — the pump
  has just finished transmitting, the bus is quiet and the processor is
  fully up.

## 1.6.0

- **Registration storm: root cause found and fixed.** The periodic 5-minute
  0x41 poll could collide with the pump's own RS485 traffic and crash the
  pump's comms processor, leaving it deaf for ~24 h (re-registering every
  2 s, ignoring all replies — a live capture showed even the manufacturer
  cloud's identical acks being ignored). The bridge now polls exactly once,
  ~2 s after the pump connects (matching the cloud's observed behaviour),
  and never mid-session. Settings changes still arrive: the pump re-pushes
  the settings block whenever a value changes.
- Bridge status `registration_storm` action text now notes the pump
  self-recovers after ~24 h (power-cycle at the breaker to clear it sooner).

## 1.5.1

- New `cloud_capture` option (diagnostic): transparently relay the pump to
  the manufacturer cloud while logging every frame in both directions, to
  capture how the cloud handles the recurring registration storm. Telemetry
  stays visible read-only in Home Assistant; local commands and the 5-min
  poll are disabled while capturing.
- Log the full registration payload (tid + all bytes) and each 5-min poll,
  to pinpoint the storm trigger.

## 1.5.0

- New **Bridge status** diagnostic sensor: `ok` / `registration_storm` /
  `no_telemetry` / `pump_disconnected`, with `detail`, `action`, `since`,
  `count` and `last_telemetry` attributes, plus a **Last telemetry**
  timestamp sensor. Detects the "registration storm" wedge (pump re-sends
  its 0x41 registration every ~2 s and ignores replies — fixed only by
  power-cycling the pump at the breaker, seen 2026-07-10).
- Telemetry entities (climate, temperatures, compressor, fault) now become
  **unavailable** when telemetry stops, instead of showing stale values
  (dedicated availability topic + MQTT last-will if the add-on dies).
- Registration frames are no longer logged on every occurrence during a
  storm (first 3, then every 100th).

## 1.4.0

- Add **Compressor output rate** sensor (reg 1006, %), verified against the app
  (0% stopped, 100% at full load).

## 1.3.3

- Fix **Ambient temperature** never updating: the periodic 0x41 poll (every
  30 s) suppressed the pump's ambient block (pushed ~once per 100 s). The
  bridge now relies on the pump's own pushes and polls only every ~5 min just
  to refresh the settings block; the post-command poll was removed (the pump
  re-pushes settings on change anyway).

## 1.3.2

- Fix the real cause of the MQTT reconnect loop (and the resulting pump
  instability / missing ambient): the MQTT socket kept the 10 s connect-time
  read timeout, so the reader treated 10 s of broker silence as a disconnect
  and reconnected every ~11 s. The socket is now blocking after connect; the
  keepalive PING maintains the link. With a stable connection, the ambient
  block (pushed ~once per 100 s) is now received.

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
