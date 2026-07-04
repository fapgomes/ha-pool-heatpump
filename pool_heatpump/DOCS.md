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

All MQTT fields can be **left blank** — the add-on then inherits the broker from
the Supervisor MQTT service (the Mosquitto broker add-on).

| Option | Default | Description |
|---|---|---|
| `mqtt_host` | *(empty)* | Broker host. Leave empty to use the Supervisor MQTT service. |
| `mqtt_port` | `1883` | Broker port. |
| `mqtt_username` | *(empty)* | MQTT user (leave empty with the auto service). |
| `mqtt_password` | *(empty)* | MQTT password. |
| `module_ip` | *(empty)* | IP of the pump's WiFi module. Set it if several Hi-Flying/HF modules exist on the LAN. If empty, the add-on uses the module connected to it, or auto-discovers. |
| `bridge_host` | *(empty)* | HA host IP the module should connect to. Auto-detected if empty. |
| `cloud_host` | `www.fzdbiology.com` | Where **Restore** points the module back to. |
| `cloud_port` | `502` | Cloud port for **Restore**. |

## Entities

- `climate.pool_heat_pump` — current water temperature, target temperature
  (whole °C, range 15–40), and heat/off.
- Secondary temperature sensor.
- **Module target** sensor — where the WiFi module currently points.
- **Adopt module (point to HA)** button — repoint the module at this add-on.
- **Restore module to cloud** button — put the module back on the cloud.

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
