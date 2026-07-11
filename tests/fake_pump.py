#!/usr/bin/env python3
"""Replay pump behaviours against a locally-running bridge (no MQTT needed).

Terminal 1:  cd pool_heatpump/scripts && python3 heatpump_bridge.py
Terminal 2:  python3 tests/fake_pump.py storm|healthy|silent [host [port]]

storm    registration frame every 2 s, never telemetry (wedged pump,
         as seen 2026-07-10) -> bridge must log
         "[status] ok -> registration_storm" after ~10 frames
healthy  telemetry block every 30 s -> bridge stays "ok"
silent   connect and send nothing -> "[status] ok -> no_telemetry"
         after 5 min; kill it (Ctrl-C) and after 2 min the bridge logs
         "[status] ... -> pump_disconnected"
"""
import socket
import struct
import sys
import time


def frame(tid, unit, fc, payload):
    body = bytes([unit, fc]) + payload
    return struct.pack(">HHH", tid, 0, len(body)) + body


REG_PAYLOAD = (bytes.fromhex("000000050a")
               + bytes.fromhex("0003000334eae742e9f2"))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "storm"
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 8502
    s = socket.create_connection((host, port))
    print(f"connected to {host}:{port}, mode={mode}")
    tid = 1
    while True:
        if mode == "storm":
            s.sendall(frame(tid, 0x01, 0x41, REG_PAYLOAD))
            time.sleep(2)
        elif mode == "healthy":
            payload = (struct.pack(">HHB", 1000, 2, 4)
                       + struct.pack(">HH", 255, 0))
            s.sendall(frame(tid, 0x01, 0x10, payload))
            time.sleep(30)
        else:  # silent
            time.sleep(60)
        tid = (tid + 1) & 0xFFFF


if __name__ == "__main__":
    main()
