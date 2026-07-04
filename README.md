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

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ffapgomes%2Fha-pool-heatpump)

1. Click the button above (or **Settings → Add-ons → Add-on Store → ⋮ →
   Repositories** and add `https://github.com/fapgomes/ha-pool-heatpump`).
2. Install **Pool Heat Pump (Modbus bridge)** from the store.
3. Make sure the **Mosquitto broker** add-on is installed.
4. Start the add-on.

## Configuration (add-on Options)

**You can leave all MQTT fields blank.** When empty, the add-on inherits the
broker automatically from the Supervisor MQTT service (the Mosquitto broker
add-on) — host, port, username and password are all filled in for you. Only set
these if you use an external broker.

| Option | Leave blank? | Notes |
|---|---|---|
| `mqtt_host` / `mqtt_port` | ✅ yes | Inherited from the Supervisor MQTT service. |
| `mqtt_username` / `mqtt_password` | ✅ yes | Inherited from the Supervisor MQTT service. |
| `module_ip` | recommended to set | IP of the pump's WiFi module (e.g. `192.168.1.41`). Set it if you have more than one Hi-Flying/HF module on the LAN so the Adopt/Restore buttons target the right one. If blank, the add-on uses the module currently connected to it, or auto-discovers. |
| `bridge_host` | usually blank | Home Assistant host IP the module should connect to. Auto-detected if blank. |
| `cloud_host` / `cloud_port` | usually blank | Where **Restore** points the module back to (defaults to the AquaTemp cloud `www.fzdbiology.com:502`). |

## Point the pump's module at the add-on (one click)

The add-on adds two buttons to Home Assistant (via MQTT):

- **Adopt module (point to HA)** — repoints the WiFi module at this add-on
  (local control, no cloud).
- **Restore module to cloud** — puts it back on the manufacturer cloud.

A **Module target** sensor shows where the module currently points. Press
**Adopt**, wait ~15 s for the module to reboot, and `climate.pool_heat_pump`
appears automatically.

> ⚠️ **Local and remote are mutually exclusive.** The module supports only one
> data connection at a time. Adopting it for local Home Assistant control means
> the **manufacturer cloud and its phone app stop working** — the pump is now
> fully local. Use **Restore** to switch back to the cloud/app (which then
> disables the Home Assistant integration). You cannot have both at once.

### Manual alternative (optional)

If you prefer the command line, redirect the module over UDP 48899:

```
AT+NETP=TCP,Client,8502,<HA_HOST_IP>   # local (add-on)
AT+NETP=TCP,Client,502,www.fzdbiology.com   # back to cloud
AT+Z                                    # reboot to apply
```

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
