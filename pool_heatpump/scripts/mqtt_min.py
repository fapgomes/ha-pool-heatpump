#!/usr/bin/env python3
"""Minimal MQTT 3.1.1 client (QoS 0), stdlib only.

Supports CONNECT (optional user/pass), PUBLISH (with retain), SUBSCRIBE,
keepalive PING and PUBLISH reception via callback. Enough to publish state and
Home Assistant auto-discovery, and to receive commands.
"""
import socket
import struct
import threading
import time


def _encode_len(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        if n > 0:
            b |= 0x80
        out.append(b)
        if n == 0:
            return bytes(out)


def _str(s: str) -> bytes:
    b = s.encode()
    return struct.pack(">H", len(b)) + b


class MqttClient:
    def __init__(self, host, port=1883, username=None, password=None,
                 client_id="heatpump-bridge", keepalive=30, on_message=None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.keepalive = keepalive
        self.on_message = on_message  # callback(topic:str, payload:bytes)
        self.sock = None
        self.lock = threading.Lock()
        self._run = False

    # -- connection ---------------------------------------------------------
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        flags = 0x02  # clean session
        payload = _str(self.client_id)
        if self.username is not None:
            flags |= 0x80
            payload += _str(self.username)
        if self.password is not None:
            flags |= 0x40
            payload += _str(self.password)
        vh = _str("MQTT") + bytes([4, flags]) + struct.pack(">H", self.keepalive)
        pkt = vh + payload
        self.sock.sendall(bytes([0x10]) + _encode_len(len(pkt)) + pkt)
        hdr = self._recv_packet()
        if hdr is None or hdr[0] >> 4 != 2 or (len(hdr[1]) >= 2 and hdr[1][1] != 0):
            raise ConnectionError(f"CONNACK failed: {hdr}")
        self._run = True
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._ping_loop, daemon=True).start()

    # -- publish/subscribe --------------------------------------------------
    def publish(self, topic, payload, retain=False):
        if isinstance(payload, str):
            payload = payload.encode()
        vh = _str(topic)
        pkt = vh + payload
        flags = 0x30 | (0x01 if retain else 0x00)
        with self.lock:
            self.sock.sendall(bytes([flags]) + _encode_len(len(pkt)) + pkt)

    def subscribe(self, topic, packet_id=1):
        pkt = struct.pack(">H", packet_id) + _str(topic) + bytes([0])
        with self.lock:
            self.sock.sendall(bytes([0x82]) + _encode_len(len(pkt)) + pkt)

    # -- reception ----------------------------------------------------------
    def _recv_n(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _recv_packet(self):
        first = self._recv_n(1)
        if first is None:
            return None
        mult = 1
        length = 0
        while True:
            b = self._recv_n(1)
            if b is None:
                return None
            length += (b[0] & 0x7F) * mult
            if not (b[0] & 0x80):
                break
            mult *= 128
        body = self._recv_n(length) if length else b""
        if body is None:
            return None
        return (first[0], body)

    def _loop(self):
        while self._run:
            try:
                pkt = self._recv_packet()
            except OSError:
                break
            if pkt is None:
                break
            ptype = pkt[0] >> 4
            if ptype == 3:  # PUBLISH
                body = pkt[1]
                tlen = struct.unpack(">H", body[:2])[0]
                topic = body[2 : 2 + tlen].decode(errors="replace")
                payload = body[2 + tlen :]
                if self.on_message:
                    try:
                        self.on_message(topic, payload)
                    except Exception as e:  # noqa: BLE001
                        print(f"[mqtt] callback error: {e}", flush=True)
        self._run = False

    def _ping_loop(self):
        while self._run:
            time.sleep(max(1, self.keepalive - 5))
            if not self._run:
                break
            try:
                with self.lock:
                    self.sock.sendall(bytes([0xC0, 0x00]))
            except OSError:
                break
