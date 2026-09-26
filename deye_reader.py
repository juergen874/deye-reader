#!/usr/bin/env python3
"""
Deye 12K Hybrid Inverter (SUN-12K-SG04LP3) Modbus Reader & Web Dashboard
Reads telemetry values from Deye SUN-12K over TCP/Solarman V5.
Default IP: 192.168.188.128 | Port: 8899 | Serial: Auto-discovered or 1109501211
"""

import socket
import socketserver
import struct
import sys
import time
import json
import argparse
import urllib.request
import base64
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

try:
    from pysolarmanv5 import PySolarmanV5
    HAS_SOLARMAN = True
except ImportError:
    HAS_SOLARMAN = False

def auto_discover_serial(host: str, username: str = "admin", password: str = "admin") -> int:
    url = f"http://{host}/status.html"
    req = urllib.request.Request(url)
    auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")
    req.add_header("Authorization", f"Basic {auth}")
    
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
            candidates = re.findall(r'\b([0-9]{10})\b', html)
            for c in candidates:
                sn = int(c)
                if 1000000000 <= sn <= 4294967295:
                    return sn
    except Exception:
        pass
    return 1109501211  # Default fallback for user's inverter

def to_signed16(val: int) -> int:
    return val if val < 0x8000 else val - 0x10000

class DeyeModbusClient:
    def __init__(self, host: str, port: int = 8899, slave_id: int = 1, serial_number: int = 0, timeout: float = 4.0):
        self.host = host
        self.port = port
        self.slave_id = slave_id
        self.serial_number = serial_number
        self.timeout = timeout
        self.solarman_client = None

    def _init_solarman(self):
        if self.serial_number == 0:
            self.serial_number = auto_discover_serial(self.host)

        self.solarman_client = PySolarmanV5(
            address=self.host,
            serial=self.serial_number,
            port=self.port,
            mb_slave_id=self.slave_id,
            socket_timeout=self.timeout
        )

    def read_holding_registers(self, start_reg: int, count: int) -> list:
        if not HAS_SOLARMAN:
            raise RuntimeError("pysolarmanv5 library not installed. Run: pip install pysolarmanv5")

        if not self.solarman_client:
            self._init_solarman()

        try:
            return self.solarman_client.read_holding_registers(register_addr=start_reg, quantity=count)
        except Exception as e:
            # Close stale connection on error to allow clean reconnect next time
            try:
                if self.solarman_client and hasattr(self.solarman_client, 'sock') and self.solarman_client.sock:
                    self.solarman_client.sock.close()
            except Exception:
                pass
            self.solarman_client = None
            raise e

    def read_deye_12k_data(self) -> dict:
        data = {}

        # Read Block 1: 500 to 541 (42 registers)
        try:
            b1 = self.read_holding_registers(500, 42)
            data["status_code"] = b1[0]  # 500
            
            status_map = {0: "Standby", 1: "Self-Test", 2: "Normal (Hybrid/Netzeinspeisung)", 3: "Alarm", 4: "Störung (Fault)"}
            data["status_text"] = status_map.get(b1[0], f"Unbekannt ({b1[0]})")
            
            data["energy_grid_buy_today_kwh"] = round(b1[20] * 0.1, 2)   # 520
            data["energy_grid_sell_today_kwh"] = round(b1[21] * 0.1, 2)  # 521
            data["energy_bat_charge_today_kwh"] = round(b1[22] * 0.1, 2) # 522
            data["energy_bat_dischg_today_kwh"] = round(b1[23] * 0.1, 2) # 523
            data["energy_load_today_kwh"] = round(b1[26] * 0.1, 2)       # 526
            data["energy_pv_today_kwh"] = round(b1[29] * 0.1, 2)         # 529
            
            data["temp_dc_celsius"] = round((b1[40] - 1000) * 0.1, 1) if b1[40] >= 1000 else round(b1[40] * 0.1 - 100, 1) # 540
            data["temp_ac_celsius"] = round((b1[41] - 1000) * 0.1, 1) if b1[41] >= 1000 else round(b1[41] * 0.1 - 100, 1) # 541
        except Exception as e:
            data["block1_error"] = str(e)

        # Read Block 2: 586 to 612 (27 registers)
        try:
            b2 = self.read_holding_registers(586, 27)
            raw_bat_temp = b2[0]  # 586
            data["temp_battery_celsius"] = round((raw_bat_temp - 1000) * 0.1, 1) if raw_bat_temp >= 1000 else round(raw_bat_temp * 0.1 - 100, 1)
            data["battery_voltage_v"] = round(b2[1] * 0.01, 2)       # 587
            data["battery_soc_percent"] = b2[2]                       # 588
            data["battery_power_w"] = to_signed16(b2[3])              # 589 (+ charge, - discharge)
            data["battery_current_a"] = round(to_signed16(b2[4]) * 0.02, 2) # 590 (factor 0.02 for double current)
            
            data["grid_voltage_l1_v"] = round(b2[12] * 0.1, 1)        # 598
            data["grid_voltage_l2_v"] = round(b2[13] * 0.1, 1)        # 599
            data["grid_voltage_l3_v"] = round(b2[14] * 0.1, 1)        # 600
            
            data["grid_power_l1_w"] = to_signed16(b2[18])             # 604 (Grid Power L1)
            data["grid_power_l2_w"] = to_signed16(b2[19])             # 605 (Grid Power L2)
            data["grid_power_l3_w"] = to_signed16(b2[20])             # 606 (Grid Power L3)
            data["grid_power_total_w"] = to_signed16(b2[21])          # 607 (Total Grid Power / Bezug: + import, - export)
            
            freq_raw = b2[22] if len(b2) > 22 and b2[22] > 0 else (b2[23] if len(b2) > 23 else 5000)  # 608 / 609 (Grid Frequency)
            if freq_raw > 10000:
                freq_raw = 5000 + to_signed16(freq_raw)
            if freq_raw > 1000:
                data["grid_frequency_hz"] = round(freq_raw * 0.01, 2)
            else:
                data["grid_frequency_hz"] = round(freq_raw * 0.1, 2)
        except Exception as e:
            data["block2_error"] = str(e)

        # Read Block 3: 625 to 653 (29 registers)
        try:
            b3 = self.read_holding_registers(625, 29)
            data["inverter_power_l1_w"] = to_signed16(b3[0])          # 625
            data["inverter_power_l2_w"] = to_signed16(b3[1])          # 626
            data["inverter_power_l3_w"] = to_signed16(b3[2])          # 627
            data["inverter_power_total_w"] = to_signed16(b3[11])       # 636
            
            data["load_power_l1_w"] = to_signed16(b3[25])             # 650
            data["load_power_l2_w"] = to_signed16(b3[26])             # 651
            data["load_power_l3_w"] = to_signed16(b3[27])             # 652
            data["load_power_total_w"] = to_signed16(b3[28])          # 653
        except Exception as e:
            data["block3_error"] = str(e)

        # Read Block 4: 672 to 679 (8 registers - PV Strings)
        try:
            b4 = self.read_holding_registers(672, 8)
            # String 1: registers 676 & 677 (b4[4] & b4[5])
            data["pv1_voltage_v"] = round(b4[4] * 0.1, 1)            # 676
            data["pv1_current_a"] = round(b4[5] * 0.1, 1)            # 677
            data["pv1_power_w"] = round(data["pv1_voltage_v"] * data["pv1_current_a"], 1)
            if data["pv1_power_w"] == 0 and b4[0] > 0:
                data["pv1_power_w"] = float(b4[0])
            
            # String 2: registers 678 & 679 (b4[6] & b4[7])
            data["pv2_voltage_v"] = round(b4[6] * 0.1, 1)            # 678
            data["pv2_current_a"] = round(b4[7] * 0.1, 1)            # 679
            data["pv2_power_w"] = round(data["pv2_voltage_v"] * data["pv2_current_a"], 1)
            if data["pv2_power_w"] == 0 and b4[1] > 0:
                data["pv2_power_w"] = float(b4[1])
            
            data["pv3_voltage_v"] = 0.0
            data["pv3_current_a"] = 0.0
            data["pv3_power_w"] = 0.0
            
            data["pv4_voltage_v"] = 0.0
            data["pv4_current_a"] = 0.0
            data["pv4_power_w"] = 0.0
            
            data["pv_power_total_w"] = round(data["pv1_power_w"] + data["pv2_power_w"], 1)
        except Exception as e:
            data["block4_error"] = str(e)

        data["logger_serial"] = self.serial_number
        data["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        data["online"] = True
        return data

# --- Global telemetry storage & lock ---
latest_telemetry = {}
telemetry_lock = threading.Lock()

# --- CLI Dashboard Formatting ---
def render_terminal_dashboard(data: dict):
    C_RESET = "\033[0m"
    C_BOLD = "\033[1m"
    C_GREEN = "\033[32m"
    C_YELLOW = "\033[33m"
    C_BLUE = "\033[34m"
    C_CYAN = "\033[36m"
    C_RED = "\033[31m"

    print("\033[H\033[J", end="")  # Clear terminal
    print(f"{C_BOLD}{C_CYAN}========================================================================{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}        DEYE SUN-12K-SG04LP3 MODBUS TELEMETRIE LOG{C_RESET}")
    print(f"        Zeitstempel: {data.get('timestamp', 'N/A')} | Logger SN: {data.get('logger_serial', 'N/A')}")
    print(f"{C_BOLD}{C_CYAN}========================================================================{C_RESET}")

    st = data.get("status_text", "N/A")
    st_color = C_GREEN if "Normal" in st else C_YELLOW
    print(f"{C_BOLD}Status:{C_RESET} {st_color}{st}{C_RESET}")
    print("------------------------------------------------------------------------")

    pv_total = data.get("pv_power_total_w", 0)
    print(f"{C_BOLD}{C_GREEN}☀️  SOLAR / PV (Gesamt: {pv_total:.1f} W | Heute: {data.get('energy_pv_today_kwh', 0)} kWh){C_RESET}")
    print(f"   String 1: {data.get('pv1_power_w', 0):>6.1f} W  ({data.get('pv1_voltage_v', 0):>5.1f} V, {data.get('pv1_current_a', 0):>4.1f} A)")
    print(f"   String 2: {data.get('pv2_power_w', 0):>6.1f} W  ({data.get('pv2_voltage_v', 0):>5.1f} V, {data.get('pv2_current_a', 0):>4.1f} A)")
    print("------------------------------------------------------------------------")

    bat_pow = data.get("battery_power_w", 0)
    bat_soc = data.get("battery_soc_percent", 0)
    bat_status = "Laden" if bat_pow > 0 else ("Entladen" if bat_pow < 0 else "Standby")
    bat_color = C_GREEN if bat_pow >= 0 else C_YELLOW
    print(f"{C_BOLD}{C_CYAN}🔋 BATTERIE ({bat_soc}% | {bat_color}{bat_status} {abs(bat_pow)} W{C_RESET}){C_RESET}")
    print(f"   Spannung: {data.get('battery_voltage_v', 0):>5.2f} V | Strom: {data.get('battery_current_a', 0):>6.2f} A | Temp: {data.get('temp_battery_celsius', 0)} °C")
    print("------------------------------------------------------------------------")

    grid_pow = data.get("grid_power_total_w", 0)
    grid_status = f"Bezug ({grid_pow} W)" if grid_pow >= 0 else f"Einspeisung ({abs(grid_pow)} W)"
    grid_color = C_RED if grid_pow > 0 else C_GREEN
    print(f"{C_BOLD}{C_BLUE}🔌 NETZ (Grid: {grid_color}{grid_status}{C_RESET} | {data.get('grid_frequency_hz', 0)} Hz){C_RESET}")
    print(f"   Phase L1: {data.get('grid_voltage_l1_v', 0):>5.1f} V | {data.get('grid_power_l1_w', 0):>6} W")
    print(f"   Phase L2: {data.get('grid_voltage_l2_v', 0):>5.1f} V | {data.get('grid_power_l2_w', 0):>6} W")
    print(f"   Phase L3: {data.get('grid_voltage_l3_v', 0):>5.1f} V | {data.get('grid_power_l3_w', 0):>6} W")
    print(f"   Kauf Heute: {data.get('energy_grid_buy_today_kwh', 0)} kWh | Verkauf Heute: {data.get('energy_grid_sell_today_kwh', 0)} kWh")
    print("------------------------------------------------------------------------")

    load_pow = data.get("load_power_total_w", 0)
    print(f"{C_BOLD}{C_YELLOW}🏠 HAUSVERBRAUCH (Gesamt: {load_pow} W | Heute: {data.get('energy_load_today_kwh', 0)} kWh){C_RESET}")
    print(f"   Phase L1: {data.get('load_power_l1_w', 0):>6} W")
    print(f"   Phase L2: {data.get('load_power_l2_w', 0):>6} W")
    print(f"   Phase L3: {data.get('load_power_l3_w', 0):>6} W")
    print("------------------------------------------------------------------------")

    print(f"🌡️  Temperatur Inverter: DC Radiator: {data.get('temp_dc_celsius', 0)} °C | AC Radiator: {data.get('temp_ac_celsius', 0)} °C")
    print(f"{C_BOLD}{C_CYAN}========================================================================{C_RESET}")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <title>Deye 12K Solar Dashboard</title>
    <style>
        :root {
            --bg-base: #060913;
            --bg-card: #0d1527;
            --bg-card-inner: #142038;
            --bg-card-hover: #1a2948;
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --text-dim: #64748b;
            --accent-solar: #f59e0b;
            --accent-solar-glow: rgba(245, 158, 11, 0.22);
            --accent-bat: #10b981;
            --accent-bat-glow: rgba(16, 185, 129, 0.22);
            --accent-grid: #38bdf8;
            --accent-grid-import: #f97316;
            --accent-grid-export: #10b981;
            --accent-load: #ec4899;
            --accent-load-glow: rgba(236, 72, 153, 0.22);
            --border-color: rgba(255, 255, 255, 0.08);
            --border-highlight: rgba(255, 255, 255, 0.15);
            --danger: #ef4444;
            --card-radius: 14px;
            --font-scale: 1;
        }

        /* TV 10-Foot Mode Variables */
        body.tv-mode {
            --font-scale: 1.18;
            padding: 12px 18px;
        }

        * {
            -webkit-box-sizing: border-box;
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: #060913;
            background-color: var(--bg-base);
            color: #f8fafc;
            color: var(--text-main);
            padding: 14px 16px;
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
            overflow-x: hidden;
            font-size: calc(15px * var(--font-scale));
        }

        .container {
            max-width: 1320px;
            margin: 0 auto;
        }

        /* Focus styles for LG Magic Remote & D-Pad navigation */
        button:focus-visible, a:focus-visible, .focusable:focus-visible {
            outline: 3px solid #38bdf8;
            outline-offset: 2px;
            box-shadow: 0 0 14px rgba(56, 189, 248, 0.7);
        }

        /* Top Header */
        header {
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-flex-wrap: wrap;
            flex-wrap: wrap;
            -webkit-box-pack: justify;
            -webkit-justify-content: space-between;
            justify-content: space-between;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 10px;
            margin-bottom: 14px;
            gap: 10px;
        }

        .header-title {
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            gap: 12px;
        }

        .header-icon {
            width: 36px;
            height: 36px;
            border-radius: 10px;
            background: -webkit-linear-gradient(315deg, #f59e0b, #ec4899);
            background: linear-gradient(135deg, #f59e0b, #ec4899);
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            -webkit-box-pack: center;
            -webkit-justify-content: center;
            justify-content: center;
            font-size: 1.2rem;
            box-shadow: 0 0 16px rgba(245, 158, 11, 0.35);
        }

        .header-text h1 {
            font-size: calc(1.35rem * var(--font-scale));
            font-weight: 700;
            color: #fbbf24;
            letter-spacing: -0.3px;
            line-height: 1.2;
        }

        .header-text .sub {
            font-size: calc(0.8rem * var(--font-scale));
            color: #94a3b8;
            color: var(--text-sub);
            margin-top: 2px;
        }

        .header-controls {
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            gap: 8px;
            -webkit-flex-wrap: wrap;
            flex-wrap: wrap;
        }

        .badge {
            display: -webkit-inline-box;
            display: -webkit-inline-flex;
            display: inline-flex;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            background: #142038;
            background: var(--bg-card-inner);
            padding: 5px 12px;
            border-radius: 9999px;
            font-size: calc(0.82rem * var(--font-scale));
            font-weight: 500;
            color: #94a3b8;
            color: var(--text-sub);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border: 1px solid var(--border-color);
        }

        .badge-btn {
            cursor: pointer;
            border: 1px solid rgba(255, 255, 255, 0.12);
            background: #142038;
            color: #f8fafc;
            font-family: inherit;
            padding: 5px 11px;
            border-radius: 9999px;
            font-size: calc(0.8rem * var(--font-scale));
            font-weight: 600;
            display: -webkit-inline-box;
            display: -webkit-inline-flex;
            display: inline-flex;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            gap: 6px;
            transition: background-color 0.15s, border-color 0.15s, transform 0.15s;
        }

        .badge-btn:hover, .badge-btn:focus {
            background: #1e2c47;
            border-color: #38bdf8;
        }

        .badge-btn.active {
            background: #38bdf8;
            color: #060913;
            border-color: #38bdf8;
            font-weight: 700;
        }

        .dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: #10b981;
            box-shadow: 0 0 8px #10b981;
            display: inline-block;
            margin-right: 7px;
        }

        .dot.pulse {
            -webkit-animation: pulse-dot 1.8s infinite ease-in-out;
            animation: pulse-dot 1.8s infinite ease-in-out;
        }

        .dot.err {
            background-color: #ef4444;
            box-shadow: 0 0 8px #ef4444;
        }

        @-webkit-keyframes pulse-dot {
            0%, 100% { opacity: 1; -webkit-transform: scale(1); transform: scale(1); }
            50% { opacity: 0.4; -webkit-transform: scale(0.8); transform: scale(0.8); }
        }

        @keyframes pulse-dot {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(0.8); }
        }

        /* Power Flow Hub Diagram & Summary */
        .flow-section {
            background: #0d1527;
            background: var(--bg-card);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border: 1px solid var(--border-color);
            border-radius: var(--card-radius);
            padding: 12px 16px;
            margin-bottom: 14px;
            box-shadow: 0 6px 24px rgba(0, 0, 0, 0.35);
        }

        .flow-summary-bar {
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-flex-wrap: wrap;
            flex-wrap: wrap;
            -webkit-box-pack: justify;
            -webkit-justify-content: space-between;
            justify-content: space-between;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            gap: 8px;
        }

        .flow-item {
            -webkit-box-flex: 1;
            -webkit-flex: 1 1 140px;
            flex: 1 1 140px;
            text-align: center;
            padding: 8px 10px;
            background: #142038;
            background: var(--bg-card-inner);
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.05);
            transition: transform 0.2s;
        }

        .flow-item:hover {
            border-color: rgba(255, 255, 255, 0.15);
        }

        .flow-label {
            font-size: calc(0.74rem * var(--font-scale));
            color: #94a3b8;
            color: var(--text-sub);
            text-transform: uppercase;
            letter-spacing: 0.6px;
            font-weight: 600;
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            -webkit-box-pack: center;
            -webkit-justify-content: center;
            justify-content: center;
            gap: 5px;
        }

        .flow-val {
            font-size: calc(1.32rem * var(--font-scale));
            font-weight: 800;
            margin-top: 3px;
            letter-spacing: -0.5px;
            font-variant-numeric: tabular-nums;
        }

        .flow-sub {
            font-size: calc(0.75rem * var(--font-scale));
            color: #94a3b8;
            margin-top: 2px;
        }

        /* Flow Diagram Center Canvas */
        .flow-diagram-container {
            margin-top: 10px;
            padding-top: 10px;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-box-pack: center;
            -webkit-justify-content: center;
            justify-content: center;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
        }

        .flow-diagram {
            width: 100%;
            max-width: 680px;
            height: 120px;
            position: relative;
        }

        svg.flow-svg {
            width: 100%;
            height: 100%;
            overflow: visible;
        }

        .flow-line {
            fill: none;
            stroke: rgba(255, 255, 255, 0.12);
            stroke-width: 3;
            stroke-linecap: round;
        }

        .flow-line-active {
            stroke-dasharray: 6 6;
            -webkit-animation: flow-dash 1.2s linear infinite;
            animation: flow-dash 1.2s linear infinite;
        }

        .flow-line-reverse {
            stroke-dasharray: 6 6;
            -webkit-animation: flow-dash-rev 1.2s linear infinite;
            animation: flow-dash-rev 1.2s linear infinite;
        }

        @-webkit-keyframes flow-dash {
            to { stroke-dashoffset: -24; }
        }

        @keyframes flow-dash {
            to { stroke-dashoffset: -24; }
        }

        @-webkit-keyframes flow-dash-rev {
            to { stroke-dashoffset: 24; }
        }

        @keyframes flow-dash-rev {
            to { stroke-dashoffset: 24; }
        }

        /* 4 Main KPI Cards Grid */
        .grid {
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-flex-wrap: wrap;
            flex-wrap: wrap;
            margin: -6px;
        }

        .card {
            -webkit-box-flex: 1;
            -webkit-flex: 1 1 calc(50% - 12px);
            flex: 1 1 calc(50% - 12px);
            min-width: 300px;
            margin: 6px;
            background: #0d1527;
            background: var(--bg-card);
            border-radius: var(--card-radius);
            padding: 14px 16px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border: 1px solid var(--border-color);
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-box-orient: vertical;
            -webkit-box-direction: normal;
            -webkit-flex-direction: column;
            flex-direction: column;
            -webkit-box-pack: justify;
            -webkit-justify-content: space-between;
            justify-content: space-between;
            position: relative;
            overflow: hidden;
            transition: border-color 0.2s;
        }

        .card:hover {
            border-color: var(--border-highlight);
        }

        .card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
        }

        .card-solar::before { background: #f59e0b; }
        .card-bat::before { background: #10b981; }
        .card-grid::before { background: #38bdf8; }
        .card-load::before { background: #ec4899; }

        .card-header {
            font-size: calc(1.05rem * var(--font-scale));
            font-weight: 700;
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-box-pack: justify;
            -webkit-justify-content: space-between;
            justify-content: space-between;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            margin-bottom: 6px;
        }

        .card-title-group {
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            gap: 6px;
        }

        .metric-big {
            font-size: calc(2.35rem * var(--font-scale));
            font-weight: 800;
            margin: 4px 0 10px 0;
            letter-spacing: -0.8px;
            line-height: 1.1;
            font-variant-numeric: tabular-nums;
        }

        .solar-color { color: #f59e0b; color: var(--accent-solar); }
        .bat-color { color: #10b981; color: var(--accent-bat); }
        .grid-color { color: #38bdf8; color: var(--accent-grid); }
        .load-color { color: #ec4899; color: var(--accent-load); }

        /* Battery progress bar */
        .bat-gauge-wrap {
            width: 100%;
            height: 8px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
            overflow: hidden;
            margin: 6px 0 10px 0;
            position: relative;
        }

        .bat-gauge-fill {
            height: 100%;
            width: 0%;
            background: -webkit-linear-gradient(left, #10b981, #34d399);
            background: linear-gradient(90deg, #10b981, #34d399);
            border-radius: 4px;
            transition: width 0.4s ease, background-color 0.3s;
        }

        .sub-metrics {
            margin-top: auto;
            font-size: calc(0.85rem * var(--font-scale));
            background: #142038;
            background: var(--bg-card-inner);
            padding: 8px 12px;
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.06);
        }

        .row {
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-box-pack: justify;
            -webkit-justify-content: space-between;
            justify-content: space-between;
            -webkit-box-align: center;
            -webkit-align-items: center;
            align-items: center;
            padding: 4px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
        }

        .row:last-child {
            border-bottom: none;
        }

        .row span:first-child {
            color: #94a3b8;
            color: var(--text-sub);
        }

        .val {
            color: #f8fafc;
            color: var(--text-main);
            font-weight: 600;
            font-variant-numeric: tabular-nums;
        }

        .val-badge {
            font-size: calc(0.76rem * var(--font-scale));
            padding: 3px 8px;
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.08);
            font-weight: 600;
        }

        .badge-solar { background: rgba(245, 158, 11, 0.18); color: #fbbf24; }
        .badge-bat { background: rgba(16, 185, 129, 0.18); color: #34d399; }
        .badge-grid { background: rgba(56, 189, 248, 0.18); color: #38bdf8; }
        .badge-load { background: rgba(236, 72, 153, 0.18); color: #f472b6; }

        /* Footer */
        footer {
            margin-top: 14px;
            text-align: center;
            font-size: calc(0.78rem * var(--font-scale));
            color: #64748b;
            color: var(--text-dim);
            padding: 6px 0;
            display: -webkit-box;
            display: -webkit-flex;
            display: flex;
            -webkit-box-pack: justify;
            -webkit-justify-content: space-between;
            justify-content: space-between;
            -webkit-flex-wrap: wrap;
            flex-wrap: wrap;
            gap: 6px;
        }

        /* Large Screen & TV optimizations (1080p / 4K) */
        @media (min-width: 1400px) and (min-height: 800px) {
            body:not(.tv-mode) {
                --font-scale: 1.1;
                padding: 16px 24px;
            }
        }

        @media (max-width: 680px) {
            body { padding: 8px; }
            .card { -webkit-flex-basis: 100%; flex-basis: 100%; min-width: 100%; margin: 4px 0; }
            .metric-big { font-size: 2rem; }
            .flow-diagram-container { display: none; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Top Navigation Header -->
        <header>
            <div class="header-title">
                <div class="header-icon">⚡</div>
                <div class="header-text">
                    <h1>Deye SUN-12K Hybrid Inverter</h1>
                    <div class="sub" id="header-meta">Modbus TCP &bull; Solarman V5 &bull; Live Telemetrie</div>
                </div>
            </div>
            <div class="header-controls">
                <div class="badge" id="clock-badge">
                    <span id="live-clock">--:--:--</span>
                </div>
                <div class="badge" id="status-badge">
                    <span class="dot pulse" id="status-dot"></span>
                    <span id="last-update">Verbinde...</span>
                </div>
                <button type="button" class="badge-btn focusable" id="btn-tv" onclick="toggleTvMode()" title="10-Foot TV Ansicht umschalten (Taste T)" tabindex="0">
                    📺 TV-Modus
                </button>
                <button type="button" class="badge-btn focusable" id="btn-fullscreen" onclick="toggleFullscreen()" title="Vollbild umschalten (Taste F)" tabindex="0">
                    ⛶ Vollbild
                </button>
                <button type="button" class="badge-btn focusable" id="btn-refresh" onclick="fetchMetrics()" title="Jetzt aktualisieren (Taste R)" tabindex="0">
                    🔄
                </button>
            </div>
        </header>

        <!-- Power Summary & Live Flow Visualizer -->
        <div class="flow-section">
            <div class="flow-summary-bar">
                <div class="flow-item">
                    <div class="flow-label solar-color">☀️ Solar Erzeugung</div>
                    <div class="flow-val solar-color" id="sum-solar">-- W</div>
                    <div class="flow-sub" id="sum-solar-sub">Heute: -- kWh</div>
                </div>
                <div class="flow-item">
                    <div class="flow-label bat-color">🔋 Batteriespeicher</div>
                    <div class="flow-val bat-color" id="sum-bat">-- %</div>
                    <div class="flow-sub" id="sum-bat-sub">Standby</div>
                </div>
                <div class="flow-item">
                    <div class="flow-label grid-color">🔌 Netzanschluss</div>
                    <div class="flow-val grid-color" id="sum-grid">-- W</div>
                    <div class="flow-sub" id="sum-grid-sub">50.00 Hz</div>
                </div>
                <div class="flow-item">
                    <div class="flow-label load-color">🏠 Hausverbrauch</div>
                    <div class="flow-val load-color" id="sum-load">-- W</div>
                    <div class="flow-sub" id="sum-load-sub">Heute: -- kWh</div>
                </div>
                <div class="flow-item">
                    <div class="flow-label" style="color: #38bdf8;">🌿 Autarkiegrad</div>
                    <div class="flow-val" style="color: #38bdf8;" id="sum-autarky">-- %</div>
                    <div class="flow-sub" id="sum-autarky-sub">Solar + Akku</div>
                </div>
            </div>

            <!-- Animated SVG Flow Diagram -->
            <div class="flow-diagram-container">
                <div class="flow-diagram">
                    <svg class="flow-svg" viewBox="0 0 600 90" preserveAspectRatio="xMidYMid meet">
                        <!-- Node Definitions: PV(75, 45), BAT(225, 45), INV(300, 45), GRID(375, 45), LOAD(525, 45) -->
                        <!-- Line 1: PV -> INV -->
                        <line id="line-pv-inv" x1="100" y1="45" x2="260" y2="45" class="flow-line" />
                        <!-- Line 2: INV <-> BAT -->
                        <line id="line-inv-bat" x1="260" y1="45" x2="200" y2="45" class="flow-line" />
                        <!-- Line 3: INV <-> GRID -->
                        <line id="line-inv-grid" x1="340" y1="45" x2="400" y2="45" class="flow-line" />
                        <!-- Line 4: INV -> LOAD -->
                        <line id="line-inv-load" x1="340" y1="45" x2="500" y2="45" class="flow-line" />

                        <!-- Center Inverter Hub -->
                        <circle cx="300" cy="45" r="24" fill="#142038" stroke="#6366f1" stroke-width="2.5" />
                        <text x="300" y="42" text-anchor="middle" fill="#f8fafc" font-size="10" font-weight="700">DEYE</text>
                        <text x="300" y="55" text-anchor="middle" fill="#94a3b8" font-size="8">12K</text>

                        <!-- Solar Node -->
                        <circle cx="75" cy="45" r="20" fill="#142038" stroke="#f59e0b" stroke-width="2" />
                        <text x="75" y="49" text-anchor="middle" font-size="14">☀️</text>

                        <!-- Battery Node -->
                        <circle cx="180" cy="45" r="20" fill="#142038" stroke="#10b981" stroke-width="2" />
                        <text x="180" y="49" text-anchor="middle" font-size="14">🔋</text>

                        <!-- Grid Node -->
                        <circle cx="420" cy="45" r="20" fill="#142038" stroke="#38bdf8" stroke-width="2" />
                        <text x="420" y="49" text-anchor="middle" font-size="14">🔌</text>

                        <!-- Load Node -->
                        <circle cx="525" cy="45" r="20" fill="#142038" stroke="#ec4899" stroke-width="2" />
                        <text x="525" y="49" text-anchor="middle" font-size="14">🏠</text>
                    </svg>
                </div>
            </div>
        </div>

        <!-- 4 Detailed KPI Cards -->
        <div class="grid">
            <!-- 1. PV Solar Card -->
            <div class="card card-solar">
                <div>
                    <div class="card-header solar-color">
                        <div class="card-title-group">
                            <span>☀️</span>
                            <span>Photovoltaik (PV)</span>
                        </div>
                        <span class="val-badge badge-solar" id="pv-today-badge">Heute: -- kWh</span>
                    </div>
                    <div class="metric-big solar-color" id="pv-total">-- W</div>
                </div>
                <div class="sub-metrics">
                    <div class="row">
                        <span>String 1:</span>
                        <span class="val" id="pv1">-- W (-- V, -- A)</span>
                    </div>
                    <div class="row">
                        <span>String 2:</span>
                        <span class="val" id="pv2">-- W (-- V, -- A)</span>
                    </div>
                    <div class="row">
                        <span>Tagesertrag Solar:</span>
                        <span class="val" id="pv-today">-- kWh</span>
                    </div>
                </div>
            </div>

            <!-- 2. Battery Card -->
            <div class="card card-bat">
                <div>
                    <div class="card-header bat-color">
                        <div class="card-title-group">
                            <span>🔋</span>
                            <span>Batteriespeicher</span>
                        </div>
                        <span class="val-badge badge-bat" id="bat-status-badge">Standby</span>
                    </div>
                    <div class="metric-big bat-color" id="bat-soc">-- %</div>
                    <div class="bat-gauge-wrap">
                        <div class="bat-gauge-fill" id="bat-gauge-fill"></div>
                    </div>
                </div>
                <div class="sub-metrics">
                    <div class="row">
                        <span>Ladeleistung:</span>
                        <span class="val" id="bat-power">-- W</span>
                    </div>
                    <div class="row">
                        <span>Spannung / Strom:</span>
                        <span class="val" id="bat-vi">-- V / -- A</span>
                    </div>
                    <div class="row">
                        <span>Batterietemperatur:</span>
                        <span class="val" id="bat-temp">-- &deg;C</span>
                    </div>
                    <div class="row">
                        <span>Ladung / Entladung Heute:</span>
                        <span class="val" id="bat-today">-- / -- kWh</span>
                    </div>
                </div>
            </div>

            <!-- 3. Grid Card -->
            <div class="card card-grid">
                <div>
                    <div class="card-header grid-color">
                        <div class="card-title-group">
                            <span>🔌</span>
                            <span>Netzanschluss</span>
                        </div>
                        <span class="val-badge badge-grid" id="grid-freq">50.00 Hz</span>
                    </div>
                    <div class="metric-big grid-color" id="grid-total">-- W</div>
                </div>
                <div class="sub-metrics">
                    <div class="row">
                        <span>Phase L1:</span>
                        <span class="val" id="grid-l1">-- V / -- W</span>
                    </div>
                    <div class="row">
                        <span>Phase L2:</span>
                        <span class="val" id="grid-l2">-- V / -- W</span>
                    </div>
                    <div class="row">
                        <span>Phase L3:</span>
                        <span class="val" id="grid-l3">-- V / -- W</span>
                    </div>
                    <div class="row">
                        <span>Kauf / Verkauf Heute:</span>
                        <span class="val" id="grid-today">-- / -- kWh</span>
                    </div>
                </div>
            </div>

            <!-- 4. Load & Inverter Card -->
            <div class="card card-load">
                <div>
                    <div class="card-header load-color">
                        <div class="card-title-group">
                            <span>🏠</span>
                            <span>Hausverbrauch</span>
                        </div>
                        <span class="val-badge badge-load" id="load-today-badge">Heute: -- kWh</span>
                    </div>
                    <div class="metric-big load-color" id="load-total">-- W</div>
                </div>
                <div class="sub-metrics">
                    <div class="row">
                        <span>Phasen L1 / L2 / L3:</span>
                        <span class="val" id="load-phases">-- / -- / -- W</span>
                    </div>
                    <div class="row">
                        <span>Tagesverbrauch Haus:</span>
                        <span class="val" id="load-today">-- kWh</span>
                    </div>
                    <div class="row">
                        <span>Inverter Temp (DC / AC):</span>
                        <span class="val" id="inv-temps">-- / -- &deg;C</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Footer Info -->
        <footer>
            <div id="footer-details">Deye SUN-12K-SG04LP3 &bull; Logger SN: ---</div>
            <div>LG TV / Chrome 101 optimiert &bull; Tasten: [T] TV-Modus &bull; [F] Vollbild &bull; [R] Refresh</div>
        </footer>
    </div>

    <script>
        // --- Helper: Format Watts / kW ---
        function fmtW(w) {
            if (w === undefined || w === null || isNaN(w)) return "0 W";
            var num = Number(w);
            var abs = Math.abs(num);
            if (abs >= 1000) {
                return (num / 1000).toFixed(2) + " kW";
            }
            return Math.round(num) + " W";
        }

        function setElText(id, text) {
            var el = document.getElementById(id);
            if (el && el.textContent !== text) {
                el.textContent = text;
            }
        }

        // --- Live Clock ---
        function updateClock() {
            var now = new Date();
            var h = String(now.getHours()).padStart(2, '0');
            var m = String(now.getMinutes()).padStart(2, '0');
            var s = String(now.getSeconds()).padStart(2, '0');
            setElText('live-clock', h + ':' + m + ':' + s);
        }
        setInterval(updateClock, 1000);
        updateClock();

        // --- TV Mode & Fullscreen Management ---
        function toggleTvMode() {
            var isTv = document.body.classList.toggle('tv-mode');
            var btn = document.getElementById('btn-tv');
            if (btn) {
                if (isTv) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            }
            try {
                localStorage.setItem('deye_tv_mode', isTv ? '1' : '0');
            } catch(e) {}
        }

        // Restore TV Mode preference
        try {
            if (localStorage.getItem('deye_tv_mode') === '1') {
                document.body.classList.add('tv-mode');
                var tvBtn = document.getElementById('btn-tv');
                if (tvBtn) tvBtn.classList.add('active');
            }
        } catch(e) {}

        function toggleFullscreen() {
            if (!document.fullscreenElement) {
                if (document.documentElement.requestFullscreen) {
                    document.documentElement.requestFullscreen().catch(function(){});
                }
            } else {
                if (document.exitFullscreen) {
                    document.exitFullscreen().catch(function(){});
                }
            }
        }

        document.addEventListener('fullscreenchange', function() {
            var btn = document.getElementById('btn-fullscreen');
            if (btn) {
                if (document.fullscreenElement) {
                    btn.classList.add('active');
                    btn.textContent = '✕ Verlassen';
                } else {
                    btn.classList.remove('active');
                    btn.textContent = '⛶ Vollbild';
                }
            }
        });

        // Keyboard shortcuts for LG Magic Remote & keyboard
        window.addEventListener('keydown', function(e) {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            var key = e.key ? e.key.toLowerCase() : '';
            if (key === 'f') {
                toggleFullscreen();
            } else if (key === 't') {
                toggleTvMode();
            } else if (key === 'r') {
                fetchMetrics();
            }
        });

        // --- Update UI with Telemetry Data ---
        function updateUI(d) {
            if (!d || typeof d !== 'object' || Object.keys(d).length === 0) {
                setElText('last-update', 'Warte auf Wechselrichter...');
                return;
            }

            // Status Indicator
            var dot = document.getElementById('status-dot');
            if (dot) dot.className = 'dot pulse';
            var st = d.status_text || 'Normal';
            var timeStr = '';
            if (d.timestamp) {
                var parts = d.timestamp.split(' ');
                timeStr = parts.length > 1 ? parts[1] : d.timestamp;
            }
            setElText('last-update', (timeStr ? timeStr + ' ' : '') + '(' + st + ')');

            // Core Power Values
            var pvTot = (typeof d.pv_power_total_w === 'number') ? d.pv_power_total_w : 0;
            var batPow = (typeof d.battery_power_w === 'number') ? d.battery_power_w : 0;
            var gridPow = (typeof d.grid_power_total_w === 'number') ? d.grid_power_total_w : 0;
            var loadTot = (typeof d.load_power_total_w === 'number') ? d.load_power_total_w : 0;
            var soc = (d.battery_soc_percent !== undefined) ? d.battery_soc_percent : 0;

            // Summary Bar
            setElText('sum-solar', fmtW(pvTot));
            var pvToday = (typeof d.energy_pv_today_kwh === 'number') ? d.energy_pv_today_kwh.toFixed(1) : '0.0';
            setElText('sum-solar-sub', 'Heute: ' + pvToday + ' kWh');

            setElText('sum-bat', soc + ' %');
            var batStatusText = 'Standby';
            if (batPow > 20) {
                batStatusText = 'Laden (' + fmtW(batPow) + ')';
            } else if (batPow < -20) {
                batStatusText = 'Entladen (' + fmtW(Math.abs(batPow)) + ')';
            }
            setElText('sum-bat-sub', batStatusText);

            var gridText = (gridPow > 20) ? ('Bezug ' + fmtW(gridPow)) : ((gridPow < -20) ? ('Einspeisung ' + fmtW(Math.abs(gridPow))) : '0 W');
            var gridEl = document.getElementById('sum-grid');
            if (gridEl) {
                gridEl.textContent = (gridPow >= 0 ? '+' : '') + fmtW(gridPow);
                gridEl.style.color = (gridPow < -20) ? '#10b981' : ((gridPow > 20) ? '#f97316' : '#38bdf8');
            }
            setElText('sum-grid-sub', (typeof d.grid_frequency_hz === 'number' ? d.grid_frequency_hz.toFixed(2) : '50.00') + ' Hz');

            setElText('sum-load', fmtW(loadTot));
            var loadToday = (typeof d.energy_load_today_kwh === 'number') ? d.energy_load_today_kwh.toFixed(1) : '0.0';
            setElText('sum-load-sub', 'Heute: ' + loadToday + ' kWh');

            // Autarky calculation
            var autarky = 100;
            if (loadTot > 0) {
                var gridImport = Math.max(0, gridPow);
                autarky = Math.round(Math.max(0, Math.min(100, (1 - (gridImport / loadTot)) * 100)));
            } else {
                autarky = 100;
            }
            setElText('sum-autarky', autarky + ' %');

            // 1. PV Card
            setElText('pv-total', fmtW(pvTot));
            setElText('pv-today-badge', 'Heute: ' + pvToday + ' kWh');
            setElText('pv-today', pvToday + ' kWh');

            var pv1V = (typeof d.pv1_voltage_v === 'number') ? d.pv1_voltage_v.toFixed(1) : '0.0';
            var pv1I = (typeof d.pv1_current_a === 'number') ? d.pv1_current_a.toFixed(1) : '0.0';
            var pv1W = (typeof d.pv1_power_w === 'number') ? d.pv1_power_w.toFixed(0) : '0';
            setElText('pv1', pv1W + ' W (' + pv1V + ' V, ' + pv1I + ' A)');

            var pv2V = (typeof d.pv2_voltage_v === 'number') ? d.pv2_voltage_v.toFixed(1) : '0.0';
            var pv2I = (typeof d.pv2_current_a === 'number') ? d.pv2_current_a.toFixed(1) : '0.0';
            var pv2W = (typeof d.pv2_power_w === 'number') ? d.pv2_power_w.toFixed(0) : '0';
            setElText('pv2', pv2W + ' W (' + pv2V + ' V, ' + pv2I + ' A)');

            // 2. Battery Card
            setElText('bat-soc', soc + ' %');
            setElText('bat-status-badge', batStatusText);
            setElText('bat-power', (batPow >= 0 ? '+' : '') + fmtW(batPow));
            var batV = (typeof d.battery_voltage_v === 'number') ? d.battery_voltage_v.toFixed(1) : '0.0';
            var batI = (typeof d.battery_current_a === 'number') ? d.battery_current_a.toFixed(1) : '0.0';
            setElText('bat-vi', batV + ' V / ' + batI + ' A');
            setElText('bat-temp', (d.temp_battery_celsius !== undefined ? d.temp_battery_celsius : 0) + ' \u00B0C');
            var batChgToday = (typeof d.energy_bat_charge_today_kwh === 'number') ? d.energy_bat_charge_today_kwh.toFixed(1) : '0.0';
            var batDisToday = (typeof d.energy_bat_dischg_today_kwh === 'number') ? d.energy_bat_dischg_today_kwh.toFixed(1) : '0.0';
            setElText('bat-today', batChgToday + ' / ' + batDisToday + ' kWh');

            var batGauge = document.getElementById('bat-gauge-fill');
            if (batGauge) {
                var safeSoc = Math.max(0, Math.min(100, soc));
                batGauge.style.width = safeSoc + '%';
                if (safeSoc > 35) {
                    batGauge.style.background = 'linear-gradient(90deg, #10b981, #34d399)';
                } else if (safeSoc > 15) {
                    batGauge.style.background = 'linear-gradient(90deg, #f59e0b, #fbbf24)';
                } else {
                    batGauge.style.background = 'linear-gradient(90deg, #ef4444, #f87171)';
                }
            }

            // 3. Grid Card
            var gridCardTot = document.getElementById('grid-total');
            if (gridCardTot) {
                gridCardTot.textContent = (gridPow >= 0 ? '+' : '') + fmtW(gridPow);
                gridCardTot.style.color = (gridPow < -20) ? '#10b981' : ((gridPow > 20) ? '#f97316' : '#38bdf8');
            }
            setElText('grid-freq', (typeof d.grid_frequency_hz === 'number' ? d.grid_frequency_hz.toFixed(2) : '50.00') + ' Hz');
            var gL1V = (typeof d.grid_voltage_l1_v === 'number') ? d.grid_voltage_l1_v.toFixed(1) : '0.0';
            var gL1W = (typeof d.grid_power_l1_w === 'number') ? d.grid_power_l1_w : 0;
            setElText('grid-l1', gL1V + ' V / ' + gL1W + ' W');

            var gL2V = (typeof d.grid_voltage_l2_v === 'number') ? d.grid_voltage_l2_v.toFixed(1) : '0.0';
            var gL2W = (typeof d.grid_power_l2_w === 'number') ? d.grid_power_l2_w : 0;
            setElText('grid-l2', gL2V + ' V / ' + gL2W + ' W');

            var gL3V = (typeof d.grid_voltage_l3_v === 'number') ? d.grid_voltage_l3_v.toFixed(1) : '0.0';
            var gL3W = (typeof d.grid_power_l3_w === 'number') ? d.grid_power_l3_w : 0;
            setElText('grid-l3', gL3V + ' V / ' + gL3W + ' W');

            var buyToday = (typeof d.energy_grid_buy_today_kwh === 'number') ? d.energy_grid_buy_today_kwh.toFixed(1) : '0.0';
            var sellToday = (typeof d.energy_grid_sell_today_kwh === 'number') ? d.energy_grid_sell_today_kwh.toFixed(1) : '0.0';
            setElText('grid-today', 'Kauf: ' + buyToday + ' / Verk: ' + sellToday + ' kWh');

            // 4. Load Card
            setElText('load-total', fmtW(loadTot));
            setElText('load-today-badge', 'Heute: ' + loadToday + ' kWh');
            var lL1 = (typeof d.load_power_l1_w === 'number') ? d.load_power_l1_w : 0;
            var lL2 = (typeof d.load_power_l2_w === 'number') ? d.load_power_l2_w : 0;
            var lL3 = (typeof d.load_power_l3_w === 'number') ? d.load_power_l3_w : 0;
            setElText('load-phases', lL1 + ' / ' + lL2 + ' / ' + lL3 + ' W');
            setElText('load-today', loadToday + ' kWh');

            var dcTemp = (typeof d.temp_dc_celsius === 'number') ? d.temp_dc_celsius.toFixed(1) : '0';
            var acTemp = (typeof d.temp_ac_celsius === 'number') ? d.temp_ac_celsius.toFixed(1) : '0';
            setElText('inv-temps', dcTemp + ' \u00B0C / ' + acTemp + ' \u00B0C');

            // 5. Flow Diagram Active Line Classes
            var lPv = document.getElementById('line-pv-inv');
            if (lPv) {
                lPv.className.baseVal = (pvTot > 20) ? 'flow-line flow-line-active' : 'flow-line';
                lPv.style.stroke = (pvTot > 20) ? '#f59e0b' : 'rgba(255,255,255,0.12)';
            }

            var lBat = document.getElementById('line-inv-bat');
            if (lBat) {
                if (batPow > 20) {
                    lBat.className.baseVal = 'flow-line flow-line-reverse'; // Charging
                    lBat.style.stroke = '#10b981';
                } else if (batPow < -20) {
                    lBat.className.baseVal = 'flow-line flow-line-active'; // Discharging
                    lBat.style.stroke = '#34d399';
                } else {
                    lBat.className.baseVal = 'flow-line';
                    lBat.style.stroke = 'rgba(255,255,255,0.12)';
                }
            }

            var lGrid = document.getElementById('line-inv-grid');
            if (lGrid) {
                if (gridPow < -20) {
                    lGrid.className.baseVal = 'flow-line flow-line-active'; // Export
                    lGrid.style.stroke = '#10b981';
                } else if (gridPow > 20) {
                    lGrid.className.baseVal = 'flow-line flow-line-reverse'; // Import
                    lGrid.style.stroke = '#f97316';
                } else {
                    lGrid.className.baseVal = 'flow-line';
                    lGrid.style.stroke = 'rgba(255,255,255,0.12)';
                }
            }

            var lLoad = document.getElementById('line-inv-load');
            if (lLoad) {
                lLoad.className.baseVal = (loadTot > 20) ? 'flow-line flow-line-active' : 'flow-line';
                lLoad.style.stroke = (loadTot > 20) ? '#ec4899' : 'rgba(255,255,255,0.12)';
            }

            // Footer
            if (d.logger_serial) {
                setElText('footer-details', 'Deye SUN-12K-SG04LP3 \u2022 Logger SN: ' + d.logger_serial + ' \u2022 ' + (d.timestamp || ''));
            }
        }

        // --- Fetch Metrics with AbortController for Chrome 101 / TV Standby ---
        var currentAbortController = null;
        var pollTimer = null;

        function fetchMetrics() {
            if (currentAbortController) {
                currentAbortController.abort();
            }
            currentAbortController = new AbortController();
            var timeoutId = setTimeout(function() {
                if (currentAbortController) currentAbortController.abort();
            }, 3500);

            fetch('/api/data?_t=' + Date.now(), {
                signal: currentAbortController.signal,
                cache: 'no-store'
            })
            .then(function(resp) {
                clearTimeout(timeoutId);
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            })
            .then(function(data) {
                updateUI(data);
            })
            .catch(function(err) {
                if (err.name === 'AbortError') return;
                var dot = document.getElementById('status-dot');
                if (dot) dot.className = 'dot err';
                var lu = document.getElementById('last-update');
                if (lu) lu.textContent = 'Verbindungsproblem';
            });
        }

        // --- TV Standby / Visibility change handling ---
        document.addEventListener('visibilitychange', function() {
            if (document.hidden) {
                if (pollTimer) clearInterval(pollTimer);
            } else {
                fetchMetrics();
                if (pollTimer) clearInterval(pollTimer);
                pollTimer = setInterval(fetchMetrics, 2500);
            }
        });

        // Initialize immediately with server-injected data if available
        var initialData = /*__INITIAL_DATA__*/ null;
        if (initialData && typeof initialData === 'object' && initialData.status_code !== undefined) {
            updateUI(initialData);
        }

        // Start polling every 2.5s
        fetchMetrics();
        pollTimer = setInterval(fetchMetrics, 2500);
    </script>
</body>
</html>
"""

class WebDashboardServer(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_HEAD(self):
        self._handle_response(include_body=False)

    def do_GET(self):
        self._handle_response(include_body=True)

    def _handle_response(self, include_body: bool = True):
        parsed = self.path.split("?")[0]
        try:
            if parsed == "/api/data":
                with telemetry_lock:
                    payload = json.dumps(latest_telemetry).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                if include_body:
                    self.wfile.write(payload)
            elif parsed == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
            else:
                with telemetry_lock:
                    init_data_str = json.dumps(latest_telemetry) if (latest_telemetry and "status_code" in latest_telemetry) else "null"
                rendered_html = HTML_TEMPLATE.replace("/*__INITIAL_DATA__*/ null", init_data_str)
                payload = rendered_html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                if include_body:
                    self.wfile.write(payload)
        except Exception:
            pass

    def log_message(self, format, *args):
        # Suppress stdout access logs to keep terminal display clean
        pass

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def run_web_server(port: int):
    try:
        server = ThreadedHTTPServer(("0.0.0.0", port), WebDashboardServer)
        server.serve_forever()
    except Exception as e:
        print(f"Webserver-Fehler auf Port {port}: {e}", file=sys.stderr)

def main():
    parser = argparse.ArgumentParser(description="Read Deye 12K Hybrid Inverter via Modbus TCP / Solarman V5")
    parser.add_argument("--host", type=str, default="192.168.188.128", help="Deye Inverter IP Address (Default: 192.168.188.128)")
    parser.add_argument("--port", type=int, default=8899, help="Modbus TCP Port (Default: 8899)")
    parser.add_argument("--slave-id", type=int, default=1, help="Modbus Slave ID / Unit ID (Default: 1)")
    parser.add_argument("--serial", type=int, default=1109501211, help="Logger Serial Number (Default: 1109501211)")
    parser.add_argument("--watch", type=int, nargs="?", const=3, help="Continuously poll every N seconds (Default: 3s)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON data")
    parser.add_argument("--web", type=int, nargs="?", const=8080, help="Start Web Dashboard server on port (Default: 8080)")

    args = parser.parse_args()

    client = DeyeModbusClient(
        host=args.host,
        port=args.port,
        slave_id=args.slave_id,
        serial_number=args.serial
    )

    global latest_telemetry

    if args.web:
        web_port = args.web
        print(f"Starte Web Dashboard Server auf http://0.0.0.0:{web_port} ...")
        t = threading.Thread(target=run_web_server, args=(web_port,), daemon=True)
        t.start()

    try:
        while True:
            try:
                data = client.read_deye_12k_data()
                with telemetry_lock:
                    latest_telemetry = data

                if args.json:
                    print(json.dumps(data, indent=2, ensure_ascii=False))
                elif not args.web and not args.watch:
                    render_terminal_dashboard(data)
                    break
                elif args.watch:
                    render_terminal_dashboard(data)
                elif args.web and not args.watch:
                    render_terminal_dashboard(data)
                    print(f"\n🌐 Web Dashboard aktiv auf: http://localhost:{args.web}")
                    print("Drücke Strg+C zum Beenden.")

            except Exception as e:
                print(f"Fehler beim Auslesen des Deye Inverters ({args.host}:{args.port}): {e}", file=sys.stderr)
                if not args.watch and not args.web:
                    sys.exit(1)
            
            poll_interval = args.watch if args.watch else (3 if args.web else 0)
            if poll_interval > 0:
                time.sleep(poll_interval)
            else:
                break
    except KeyboardInterrupt:
        print("\nBeendet.")

if __name__ == "__main__":
    main()
