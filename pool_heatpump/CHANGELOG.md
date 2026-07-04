# Changelog

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
