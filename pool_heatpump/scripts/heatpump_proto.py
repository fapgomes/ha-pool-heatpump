#!/usr/bin/env python3
"""Protocolo da bomba de calor Neoboost sobre o módulo DOTELS (Modbus TCP/MBAP).

A placa da bomba é o *master*: liga-se ao servidor (originalmente a cloud) e
EMPURRA telemetria com FC 0x10 (Write Multiple Registers), unit 0x01. O servidor
responde só com ACK. Para comandar, o servidor ENVIA FC 0x06 (Write Single
Register), unit 0x81, para registos do bloco 2000.

Este módulo só faz parsing/construção de frames MBAP — sem I/O de rede.
"""
import struct
from dataclasses import dataclass

FC_WRITE_SINGLE = 0x06
FC_WRITE_MULTI = 0x10
FC_REGISTER = 0x41  # proprietária (registo/heartbeat)

UNIT_TELEMETRY = 0x01
UNIT_COMMAND = 0x81


@dataclass
class Frame:
    tid: int
    unit: int
    fc: int
    payload: bytes  # tudo depois do fc


def parse_frames(buf: bytes):
    """Extrai frames MBAP completos de buf. Devolve (frames, resto)."""
    frames = []
    i = 0
    while len(buf) - i >= 8:
        tid, proto, length = struct.unpack(">HHH", buf[i : i + 6])
        if proto != 0:
            # dessincronizado; salta um byte
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
    """ACK a FC 0x10: ecoa endereço inicial + quantidade (4 bytes)."""
    start, qty = struct.unpack(">HH", f.payload[:4])
    return build(f.tid, f.unit, f.fc, struct.pack(">HH", start, qty))


def ack_register(f: Frame) -> bytes:
    """ACK a FC 0x41 (registo): ecoa os primeiros 4 bytes do payload."""
    return build(f.tid, f.unit, f.fc, f.payload[:4])


def decode_write_multi(f: Frame):
    """Devolve (start_addr, [valores uint16]) de um FC 0x10."""
    start, qty = struct.unpack(">HH", f.payload[:4])
    bytecount = f.payload[4]
    data = f.payload[5 : 5 + bytecount]
    values = list(struct.unpack(f">{bytecount // 2}H", data))
    return start, values


def cmd_write_single(tid: int, addr: int, value: int) -> bytes:
    """Frame de comando FC 0x06 (unit 0x81) para escrever um registo."""
    return build(tid, UNIT_COMMAND, FC_WRITE_SINGLE, struct.pack(">HH", addr, value))


def s16(v: int) -> int:
    """uint16 -> int16 com sinal (temperaturas negativas)."""
    return v - 0x10000 if v >= 0x8000 else v
