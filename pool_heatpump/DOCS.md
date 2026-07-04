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

| Option | Default | Description |
|---|---|---|
| `mqtt_host` | *(empty)* | Broker host. Leave empty to use the Supervisor MQTT service. |
| `mqtt_port` | `1883` | Broker port. |
| `mqtt_username` | *(empty)* | MQTT user (leave empty with the auto service). |
| `mqtt_password` | *(empty)* | MQTT password. |

## Entities

- `climate.pool_heat_pump` — current water temperature, target temperature
  (whole °C, range 15–40), and heat/off.
- Additional temperature sensor (secondary probe).

## Redirecting the module (Hi-Flying / DOTELS example)

Over UDP 48899 from any PC on the LAN:

```
AT+NETP=TCP,Client,8502,<HA_HOST_IP>
AT+Z
```

To revert to the cloud: `AT+NETP=TCP,Client,502,<cloud-host>` + `AT+Z`.

## Notes and limitations

- The pump only fully reports the settings block (target temp/power/mode) right
  after registration or when a value changes; the add-on periodically sends a
  `0x41` query to refresh it.
- Only one data connection is supported by the module at a time — running this
  add-on means the manufacturer app no longer works (this is intentional:
  fully local).
- Register scaling was validated on a Neoboost Full Inverter unit; other clones
  in the family should match but verify the target-temperature behaviour.
