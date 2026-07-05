#!/usr/bin/env python3
"""Minimal, resilient MQTT 3.1.1 client (QoS 0), stdlib only.

Supports CONNECT (optional user/pass), PUBLISH (with retain), SUBSCRIBE,
keepalive PING and PUBLISH reception via callback. Reconnects automatically
and calls on_connect after every (re)connection so discovery/subscriptions can
be re-established. Enough for Home Assistant auto-discovery + commands.
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
                 client_id="heatpump-bridge", keepalive=30,
                 on_message=None, on_connect=None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.keepalive = keepalive
        self.on_message = on_message      # callback(topic:str, payload:bytes)
        self.on_connect = on_connect      # callback() after each (re)connect
        self.sock = None
        self.lock = threading.Lock()
        self.connected = False

    # -- lifecycle ----------------------------------------------------------
    def start(self):
        """Begin the keeper thread that connects and keeps the link alive."""
        threading.Thread(target=self._keeper, daemon=True).start()

    def _open(self):
        sock = socket.create_connection((self.host, self.port), timeout=10)
        flags = 0x02  # clean session
        payload = _str(self.client_id)
        if self.username:
            flags |= 0x80
            payload += _str(self.username)
        if self.password:
            flags |= 0x40
            payload += _str(self.password)
        vh = _str("MQTT") + bytes([4, flags]) + struct.pack(">H", self.keepalive)
        pkt = vh + payload
        sock.sendall(bytes([0x10]) + _encode_len(len(pkt)) + pkt)
        hdr = self._recv_packet(sock)
        if hdr is None or hdr[0] >> 4 != 2 or (len(hdr[1]) >= 2 and hdr[1][1] != 0):
            sock.close()
            raise ConnectionError(f"CONNACK failed: {hdr}")
        # clear the connect-time read timeout so the reader blocks on recv
        # instead of timing out every 10 s (which looked like a disconnect and
        # caused an endless reconnect loop); the keepalive PING keeps it alive
        sock.settimeout(None)
        self.sock = sock
        self.connected = True
        threading.Thread(target=self._reader, args=(sock,), daemon=True).start()
        if self.on_connect:
            try:
                self.on_connect()
            except Exception as e:  # noqa: BLE001
                print(f"[mqtt] on_connect error: {e}", flush=True)

    def _keeper(self):
        backoff = 1
        last_ping = 0.0
        while True:
            if not self.connected:
                try:
                    self._open()
                    print(f"[mqtt] connected to {self.host}:{self.port}",
                          flush=True)
                    backoff = 1
                    last_ping = time.monotonic()
                except OSError as e:
                    print(f"[mqtt] connect failed ({e}); retry in {backoff}s",
                          flush=True)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                    continue
            # connected: send keepalive pings
            if time.monotonic() - last_ping >= max(1, self.keepalive - 5):
                try:
                    with self.lock:
                        self.sock.sendall(bytes([0xC0, 0x00]))
                    last_ping = time.monotonic()
                except OSError:
                    self._drop()
            time.sleep(1)

    def _drop(self):
        self.connected = False
        try:
            self.sock.close()
        except OSError:
            pass

    # -- publish / subscribe ------------------------------------------------
    def publish(self, topic, payload, retain=False):
        if not self.connected:
            return False
        if isinstance(payload, str):
            payload = payload.encode()
        pkt = _str(topic) + payload
        flags = 0x30 | (0x01 if retain else 0x00)
        try:
            with self.lock:
                self.sock.sendall(bytes([flags]) + _encode_len(len(pkt)) + pkt)
            return True
        except OSError:
            self._drop()
            return False

    def subscribe(self, topic, packet_id=1):
        if not self.connected:
            return False
        pkt = struct.pack(">H", packet_id) + _str(topic) + bytes([0])
        try:
            with self.lock:
                self.sock.sendall(bytes([0x82]) + _encode_len(len(pkt)) + pkt)
            return True
        except OSError:
            self._drop()
            return False

    # -- reception ----------------------------------------------------------
    @staticmethod
    def _recv_n(sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _recv_packet(self, sock):
        first = self._recv_n(sock, 1)
        if first is None:
            return None
        mult = 1
        length = 0
        while True:
            b = self._recv_n(sock, 1)
            if b is None:
                return None
            length += (b[0] & 0x7F) * mult
            if not (b[0] & 0x80):
                break
            mult *= 128
        body = self._recv_n(sock, length) if length else b""
        if body is None:
            return None
        return (first[0], body)

    def _reader(self, sock):
        while self.connected and self.sock is sock:
            try:
                pkt = self._recv_packet(sock)
            except OSError:
                break
            if pkt is None:
                break
            if pkt[0] >> 4 == 3:  # PUBLISH
                body = pkt[1]
                tlen = struct.unpack(">H", body[:2])[0]
                topic = body[2 : 2 + tlen].decode(errors="replace")
                payload = body[2 + tlen :]
                if self.on_message:
                    try:
                        self.on_message(topic, payload)
                    except Exception as e:  # noqa: BLE001
                        print(f"[mqtt] callback error: {e}", flush=True)
        if self.sock is sock:
            self._drop()
