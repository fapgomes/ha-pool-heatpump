#!/usr/bin/env python3
"""Ponte local para a bomba de calor Neoboost — substitui a cloud fzdbiology.

A placa da bomba liga-se aqui (via módulo DOTELS em modo TCP Client). A ponte:
  - responde ao registo (FC 0x41) e faz ACK da telemetria empurrada (FC 0x10);
  - descodifica os registos para um dicionário de estado;
  - aceita comandos numa porta de controlo local (127.0.0.1:CTRL_PORT) e injecta-os
    na ligação da bomba como FC 0x06 (unit 0x81).

Porta de controlo (linha de texto):
  get                 -> imprime o estado JSON atual
  set <addr> <valor>  -> escreve um registo (ex.: "set 2004 30" = setpoint 30°C)
  setpoint <c>        -> atalho para reg 2004
  power <0|1>         -> atalho para reg 2001
  mode <n>            -> atalho para reg 2000

Sem dependências externas (stdlib).
"""
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import heatpump_proto as p
from mqtt_min import MqttClient

LISTEN_PUMP = ("0.0.0.0", 8502)
LISTEN_CTRL = ("127.0.0.1", 9000)

REG_MODE = 2000
REG_POWER = 2001
REG_SETPOINT = 2004

POLL_QUERY = bytes.fromhex("000000000009814100000001020000")

# tópicos MQTT / Home Assistant
DISCOVERY_PREFIX = "homeassistant"
NODE = "pool_heat_pump"
BASE = f"heatpump/{NODE}"
CONF_PATH = os.path.join(os.path.dirname(__file__), "heatpump_bridge.conf")


class Bridge:
    def __init__(self, mqtt_conf=None):
        self.regs = {}  # addr -> uint16
        self.lock = threading.Lock()
        self.pump_sock = None
        self.tid = 0x1000
        self.last_update = 0.0
        self.mqtt = None
        self.mqtt_conf = mqtt_conf

    # -- estado -------------------------------------------------------------
    def store(self, start, values):
        with self.lock:
            for i, v in enumerate(values):
                self.regs[start + i] = v
            self.last_update = time.monotonic()

    def state(self):
        with self.lock:
            r = self.regs
            age = time.monotonic() - self.last_update if self.last_update else None
            return {
                "power": r.get(REG_POWER),
                "mode": r.get(REG_MODE),
                "setpoint_c": r.get(REG_SETPOINT),
                "water_temp_c": p.s16(r.get(1001, 0)) / 10 if 1001 in r else None,
                "temp_1003_c": p.s16(r.get(1003, 0)) / 10 if 1003 in r else None,
                "regs_2000": [r.get(2000 + i) for i in range(7)],
                "n_regs": len(r),
                "age_s": round(age, 1) if age is not None else None,
            }

    # -- comandos -----------------------------------------------------------
    def send_write(self, addr, value):
        sock = self.pump_sock
        if sock is None:
            return "erro: bomba não ligada"
        self.tid = (self.tid + 1) & 0xFFFF
        frame = p.cmd_write_single(self.tid, addr, value)
        try:
            sock.sendall(frame)
            return f"ok: reg{addr} <- {value} (frame {frame.hex()})"
        except OSError as e:
            return f"erro ao enviar: {e}"

    def send_raw(self, frame):
        sock = self.pump_sock
        if sock is None:
            return "erro: bomba não ligada"
        try:
            sock.sendall(frame)
            return f"ok: enviado {frame.hex()}"
        except OSError as e:
            return f"erro ao enviar: {e}"

    # -- loop da bomba ------------------------------------------------------
    def handle_pump(self, sock, addr):
        print(f"[bomba] ligada de {addr}", flush=True)
        self.pump_sock = sock
        buf = b""
        sock.settimeout(120)
        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                buf += data
                frames, buf = p.parse_frames(buf)
                for f in frames:
                    self.on_frame(sock, f)
        except OSError as e:
            print(f"[bomba] erro: {e}", flush=True)
        finally:
            print("[bomba] desligada", flush=True)
            if self.pump_sock is sock:
                self.pump_sock = None
            sock.close()

    def on_frame(self, sock, f):
        if f.unit == p.UNIT_TELEMETRY and f.fc == p.FC_WRITE_MULTI:
            start, values = p.decode_write_multi(f)
            self.store(start, values)
            sock.sendall(p.ack_write_multi(f))
            print(f"[bloco] {start} x{len(values)}", flush=True)
        elif f.unit == p.UNIT_TELEMETRY and f.fc == p.FC_REGISTER:
            sock.sendall(p.ack_register(f))
            print(f"[bomba] registo MAC {f.payload[5:].hex()}", flush=True)
        elif f.unit == p.UNIT_COMMAND and f.fc == p.FC_WRITE_SINGLE:
            # eco de um comando nosso — confirmação da bomba
            pass
        else:
            print(f"[bomba] frame inesperado unit={f.unit:#x} fc={f.fc:#x} "
                  f"payload={f.payload.hex(' ')}", flush=True)

    # -- loop de controlo ---------------------------------------------------
    def handle_ctrl(self, sock, addr):
        f = sock.makefile("rw")
        for line in f:
            line = line.strip()
            if not line:
                continue
            reply = self.dispatch(line)
            f.write(reply + "\n")
            f.flush()
        sock.close()

    def dispatch(self, line):
        parts = line.split()
        cmd = parts[0].lower()
        try:
            if cmd == "get":
                return json.dumps(self.state())
            if cmd == "set":
                return self.send_write(int(parts[1]), int(parts[2]))
            if cmd == "setpoint":
                return self.send_write(REG_SETPOINT, int(parts[1]))
            if cmd == "power":
                return self.send_write(REG_POWER, int(parts[1]))
            if cmd == "mode":
                return self.send_write(REG_MODE, int(parts[1]))
            if cmd == "poll":
                # replica a query 0x41 que a cloud usa para pedir o dump completo
                return self.send_raw(POLL_QUERY)
            if cmd == "raw":
                return self.send_raw(bytes.fromhex(parts[1]))
            return f"erro: comando desconhecido '{cmd}'"
        except (IndexError, ValueError) as e:
            return f"erro de sintaxe: {e}"

    # -- MQTT / Home Assistant ---------------------------------------------
    def mqtt_start(self):
        c = self.mqtt_conf
        self.mqtt = MqttClient(
            host=c["host"], port=c.get("port", 1883),
            username=c.get("username"), password=c.get("password"),
            client_id="heatpump-bridge", keepalive=30,
            on_message=self.on_mqtt,
        )
        self.mqtt.connect()
        self.publish_discovery()
        self.mqtt.subscribe(f"{BASE}/set/#")
        threading.Thread(target=self._state_loop, daemon=True).start()
        print(f"[mqtt] ligado a {c['host']}:{c.get('port', 1883)}", flush=True)

    def publish_discovery(self):
        dev = {
            "identifiers": [NODE],
            "name": "Pool heat pump",
            "manufacturer": "AquaTemp-family (Neoboost / DOTELS)",
            "model": "Full Inverter (local Modbus)",
        }
        avail = [{"topic": f"{BASE}/availability"}]
        # climate
        climate = {
            "name": "Pool heat pump",
            "unique_id": f"{NODE}_climate",
            "device": dev,
            "availability": avail,
            "modes": ["off", "heat"],
            "mode_state_topic": f"{BASE}/state",
            "mode_state_template": "{{ 'heat' if value_json.power == 1 else 'off' }}",
            "mode_command_topic": f"{BASE}/set/mode_hvac",
            "current_temperature_topic": f"{BASE}/state",
            "current_temperature_template": "{{ value_json.water_temp_c }}",
            "temperature_state_topic": f"{BASE}/state",
            "temperature_state_template": "{{ value_json.setpoint_c }}",
            "temperature_command_topic": f"{BASE}/set/setpoint",
            "min_temp": 15, "max_temp": 40, "temp_step": 1,
            "temperature_unit": "C",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/climate/{NODE}/config",
            json.dumps(climate), retain=True)
        # sensor extra: temperatura secundária
        sensor = {
            "name": "Pool secondary temperature",
            "unique_id": f"{NODE}_temp2",
            "device": dev, "availability": avail,
            "state_topic": f"{BASE}/state",
            "value_template": "{{ value_json.temp_1003_c }}",
            "unit_of_measurement": "°C", "device_class": "temperature",
        }
        self.mqtt.publish(
            f"{DISCOVERY_PREFIX}/sensor/{NODE}/temp2/config",
            json.dumps(sensor), retain=True)
        self.mqtt.publish(f"{BASE}/availability", "online", retain=True)

    def publish_state(self):
        if self.mqtt:
            self.mqtt.publish(f"{BASE}/state", json.dumps(self.state()), retain=True)

    def on_mqtt(self, topic, payload):
        val = payload.decode(errors="replace").strip()
        print(f"[mqtt] cmd {topic} = {val}", flush=True)
        if topic.endswith("/set/setpoint"):
            self.send_write(REG_SETPOINT, int(round(float(val))))
        elif topic.endswith("/set/mode_hvac"):
            self.send_write(REG_POWER, 0 if val == "off" else 1)
        elif topic.endswith("/set/power"):
            self.send_write(REG_POWER, int(val))
        elif topic.endswith("/set/mode"):
            self.send_write(REG_MODE, int(val))
        time.sleep(1.5)
        self.send_raw(POLL_QUERY)  # refresca o bloco 2000

    def _state_loop(self):
        while True:
            if self.pump_sock is not None:
                self.send_raw(POLL_QUERY)  # pede dump completo periódico
                time.sleep(2)
                self.publish_state()
            time.sleep(28)

    # -- arranque -----------------------------------------------------------
    def serve(self):
        if self.mqtt_conf:
            try:
                self.mqtt_start()
            except Exception as e:  # noqa: BLE001
                print(f"[mqtt] falha ao ligar: {e}", flush=True)
        threading.Thread(target=self._accept_loop,
                         args=(LISTEN_CTRL, self.handle_ctrl, "ctrl"),
                         daemon=True).start()
        self._accept_loop(LISTEN_PUMP, self.handle_pump, "bomba")

    @staticmethod
    def _accept_loop(bind, handler, name):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(bind)
        srv.listen(5)
        print(f"[{name}] à escuta em {bind}", flush=True)
        while True:
            cli, addr = srv.accept()
            threading.Thread(target=handler, args=(cli, addr), daemon=True).start()


def load_conf():
    if os.path.exists(CONF_PATH):
        with open(CONF_PATH) as f:
            return json.load(f).get("mqtt")
    return None


if __name__ == "__main__":
    Bridge(mqtt_conf=load_conf()).serve()
