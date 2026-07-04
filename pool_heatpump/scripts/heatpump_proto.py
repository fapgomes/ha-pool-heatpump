#!/usr/bin/env python3
"""Neoboost/AquaTemp pool heat pump protocol over a transparent module (Modbus TCP/MBAP).

The pump mainboard is the *master*: it connects to the server (originally the
manufacturer cloud) and PUSHES telemetry with FC 0x10 (Write Multiple
Registers), unit 0x01. The server only ACKs. To command the pump, the server
SENDS FC 0x06 (Write Single Register), unit 0x81, to registers in the 2000 block.

This module only parses/builds MBAP frames — no network I/O.
"""
import struct
from dataclasses import dataclass

FC_WRITE_SINGLE = 0x06
FC_WRITE_MULTI = 0x10
FC_REGISTER = 0x41  # proprietary (registration/heartbeat)

UNIT_TELEMETRY = 0x01
UNIT_COMMAND = 0x81


@dataclass
class Frame:
    tid: int
    unit: int
    fc: int
    payload: bytes  # everything after the fc byte


def parse_frames(buf: bytes):
    """Extract complete MBAP frames from buf. Returns (frames, remainder)."""
    frames = []
    i = 0
    while len(buf) - i >= 8:
        tid, proto, length = struct.unpack(">HHH", buf[i : i + 6])
        if proto != 0:
            # out of sync; skip one byte
            i += 1
            continue
        total = 6 + length
        if len(buf) - i < total:
            break
        unit = buf[i + 6]
        fc = buf[i + 7]
        payload = buf[i + 8 : i + total]
        frames.append(Frame(tid, unit, fc, payload))
        i += total
    return frames, buf[i:]


def build(tid: int, unit: int, fc: int, payload: bytes) -> bytes:
    body = bytes([unit, fc]) + payload
    return struct.pack(">HHH", tid, 0, len(body)) + body


def ack_write_multi(f: Frame) -> bytes:
    """ACK an FC 0x10: echo start address + quantity (4 bytes)."""
    start, qty = struct.unpack(">HH", f.payload[:4])
    return build(f.tid, f.unit, f.fc, struct.pack(">HH", start, qty))


def ack_register(f: Frame) -> bytes:
    """ACK an FC 0x41 (registration): echo the first 4 payload bytes."""
    return build(f.tid, f.unit, f.fc, f.payload[:4])


def decode_write_multi(f: Frame):
    """Return (start_addr, [uint16 values]) from an FC 0x10 frame."""
    start, qty = struct.unpack(">HH", f.payload[:4])
    bytecount = f.payload[4]
    data = f.payload[5 : 5 + bytecount]
    values = list(struct.unpack(f">{bytecount // 2}H", data))
    return start, values


def cmd_write_single(tid: int, addr: int, value: int) -> bytes:
    """Command frame FC 0x06 (unit 0x81) to write a single register."""
    return build(tid, UNIT_COMMAND, FC_WRITE_SINGLE, struct.pack(">HH", addr, value))


def s16(v: int) -> int:
    """uint16 -> signed int16 (for negative temperatures)."""
    return v - 0x10000 if v >= 0x8000 else v
