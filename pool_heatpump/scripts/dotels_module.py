#!/usr/bin/env python3
"""Manage the pump's WiFi/serial module over UDP 48899 (Hi-Flying / DOTELS AT set).

Used by the bridge to adopt the module (point it at this add-on) or restore it
to the manufacturer cloud, exposed as Home Assistant buttons.

Discovery and AT commands travel over UDP broadcast/unicast on port 48899, so
the add-on must run on the host network to reach the module on the LAN.
"""
import socket

PORT = 48899
DISCOVER = b"HF-A11ASSISTHREAD"


def discover(timeout=3):
    """Broadcast discovery. Returns list of dicts {ip, mac, name}."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    found = []
    try:
        s.sendto(DISCOVER, ("255.255.255.255", PORT))
        while True:
            try:
                data, _ = s.recvfrom(2048)
            except socket.timeout:
                break
            parts = data.decode(errors="replace").strip().split(",")
            if len(parts) >= 3:
                found.append({"ip": parts[0], "mac": parts[1], "name": parts[2]})
    finally:
        s.close()
    return found


def _session(ip, timeout=3):
    """Open a UDP socket to a module and enter AT command mode."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    addr = (ip, PORT)
    s.sendto(DISCOVER, addr)
    try:
        s.recvfrom(2048)  # ip,mac,name
    except socket.timeout:
        s.close()
        raise ConnectionError(f"module {ip} did not answer discovery")
    s.sendto(b"+ok", addr)  # ack; may echo +ERR if already in AT mode
    s.settimeout(0.5)
    try:
        s.recvfrom(2048)
    except socket.timeout:
        pass
    s.settimeout(timeout)
    return s, addr


def at(ip, command, timeout=3):
    """Send a single AT command; return the response string."""
    s, addr = _session(ip, timeout)
    try:
        s.sendto(command.encode() + b"\r", addr)
        try:
            data, _ = s.recvfrom(2048)
            return data.decode(errors="replace").strip()
        except socket.timeout:
            return "<timeout>"
    finally:
        s.close()


def get_target(ip):
    """Return the current NETP string (e.g. 'TCP,Client,502,host') or None."""
    r = at(ip, "AT+NETP")
    return r.split("=", 1)[1] if r.startswith("+ok=") else None


def set_target(ip, host, port):
    """Point the module at host:port (TCP Client) and reboot it."""
    at(ip, f"AT+NETP=TCP,Client,{port},{host}")
    at(ip, "AT+Z")


def local_ip_towards(ip):
    """Local IP of the interface used to reach `ip` (the host LAN IP)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, PORT))
        return s.getsockname()[0]
    finally:
        s.close()
