# Pool Heat Pump — Home Assistant add-on

Local, cloud-free control of AquaTemp/fzdbiology-family swimming-pool heat pumps
(sold as **Neoboost**, **POOL COMFORT**, **DOTELS-SWP**, and other AquaTemp
clones) from Home Assistant.

The pump's WiFi module is normally a **transparent WiFi↔RS485 bridge** that
tunnels the pump's Modbus-TCP/MBAP traffic to the manufacturer cloud. This
add-on **replaces that cloud**: the pump connects to the add-on instead, which
decodes the telemetry and exposes a `climate` entity (plus sensors) over MQTT
auto-discovery, and sends commands (target temperature, on/off, mode) back to
the pump.

> It does **not** depend on any particular WiFi module (Hi-Flying HF-LPT230 /
> DOTELS, Elfin EW11, USR, …) — any transparent TCP↔serial gateway works. What
> it depends on is the **pump protocol** (AquaTemp/fzdbiology family).

## How to install (HA add-on repository)

1. In Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**.
2. Add this repository URL: `https://github.com/fapgomes/ha-pool-heatpump`.
3. Install **Pool Heat Pump (Modbus bridge)** from the store.
4. Make sure the **Mosquitto broker** add-on is installed (MQTT is configured
   automatically via the Supervisor MQTT service; otherwise set host/user/pass
   in the add-on options).
5. Start the add-on.

## Point the pump's module at the add-on

Redirect the WiFi module from the cloud to the add-on (Home Assistant host IP,
port 8502). For Hi-Flying/DOTELS modules, over UDP 48899:

```
AT+NETP=TCP,Client,8502,<HA_HOST_IP>
AT+Z
```

The `climate.pool_heat_pump` entity then appears automatically.

## Protocol summary (reverse-engineered)

- Framing: **Modbus TCP (MBAP)**. The pump is the *master*.
- Telemetry: pump pushes `FC 0x10` (write multiple) to register blocks
  (100, 200, 300, 500, 600, 1000, 2000, 2100), unit `0x01`.
- Commands: server sends `FC 0x06` (write single), unit `0x81`.
- Registration/heartbeat: `FC 0x41` (proprietary). A `0x41` query triggers a
  full register dump.
- Key registers: `2004` = target temp (°C), `2001` = power (0/1),
  `2000` = mode, `1001` = water temp (÷10).

See `pool_heatpump/DOCS.md` for details.

## Credits

Reverse-engineered from a Neoboost Full Inverter pump with a DOTELS-SWP
(HF-LPT230) module. Not affiliated with AquaTemp or any manufacturer.
