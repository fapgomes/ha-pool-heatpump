# Pool Heat Pump (Modbus bridge)

Replaces the manufacturer cloud for AquaTemp/fzdbiology-family pool heat pumps.
The pump connects to this add-on (TCP, port 8502) and the add-on publishes a
`climate` entity and sensors to Home Assistant over MQTT auto-discovery.

## Requirements

- An MQTT broker (the **Mosquitto broker** add-on). MQTT settings are picked up
  automatically from the Supervisor MQTT service; if you use an external broker,
  fill in the options below.
- The pump's WiFi/serial module set to **TCP Client** pointing at this host on
  port **8502** (any transparent WiFi/Ethernet↔RS485 gateway works).

## Options

This add-on runs on the host network (to reach the pump module over UDP), and
host-network add-ons cannot use the Supervisor MQTT auto-discovery service.
**Fill in the MQTT fields manually.**

| Option | Default | Description |
|---|---|---|
| `mqtt_host` | *(empty)* | Broker IP, e.g. `192.168.1.100`. Use the HA host IP, not `core-mosquitto` (does not resolve on the host network). |
| `mqtt_port` | `1883` | Broker port. |
| `mqtt_username` | *(empty)* | MQTT user. |
| `mqtt_password` | *(empty)* | MQTT password. |
| `module_ip` | *(empty)* | IP of the pump's WiFi module. Set it if several Hi-Flying/HF modules exist on the LAN. If empty, the add-on uses the module connected to it, or auto-discovers. |
| `bridge_host` | *(empty)* | HA host IP the module should connect to. Auto-detected if empty. |
| `cloud_host` | `www.fzdbiology.com` | Where **Restore** points the module back to. |
| `cloud_port` | `502` | Cloud port for **Restore**. |

## Entities

- `climate.pool_heat_pump` — current water temperature (inlet), target
  temperature (whole °C, range 15–40), and mode (cool/heat/auto/off).
- **Inlet water temperature** sensor.
- **Outlet water temperature** sensor.
- **Ambient temperature** sensor.
- **Fault code** sensor — the unit's fault code, e.g. `P01` (no flow); `OK`
  when there is no fault.
- **Module target** sensor — where the WiFi module currently points.
- **Adopt module (point to HA)** button — repoint the module at this add-on.
- **Restore module to cloud** button — put the module back on the cloud.
- **Reboot module** button — reboot the WiFi module (`AT+Z`) to force a
  reconnect.

### Register map (reverse-engineered, verified against the app)

| Register | Meaning | Scale |
|---|---|---|
| 1003 | inlet water temperature (app's main reading) | ÷10 °C |
| 1001 | outlet water temperature | ÷10 °C |
| 307 | ambient temperature | ×1 °C |
| 1004 | fault code: high byte = ASCII letter, low byte = number (`0x5001` → `P01`) | — |
| 2000 | mode: cool=1, heat=2, auto=4 | — |
| 2001 | power (0/1) | — |
| 2004 | target temperature | ×1 °C |

## Adopt / Restore (no command line)

Press **Adopt module (point to HA)** to move the module to local control, or
**Restore module to cloud** to switch back. The add-on talks to the module over
UDP 48899 (which is why it runs on the host network).

> ⚠️ **Local and remote are mutually exclusive.** The module keeps only one data
> connection. Adopting for local Home Assistant control means the manufacturer
> cloud and phone app stop working; Restore does the opposite. You cannot run
> both at the same time.

### Manual alternative

Over UDP 48899 from any PC on the LAN:

```
AT+NETP=TCP,Client,8502,<HA_HOST_IP>          # local
AT+NETP=TCP,Client,502,www.fzdbiology.com     # cloud
AT+Z                                          # reboot to apply
```

## Notes and limitations

- The pump only fully reports the settings block (target temp/power/mode) right
  after registration or when a value changes; the add-on periodically sends a
  `0x41` query to refresh it.
- Register scaling was validated on a Neoboost Full Inverter unit; other clones
  in the family should match but verify the target-temperature behaviour.
