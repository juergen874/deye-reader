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
    <title>Deye 12K Solar Dashboard</title>
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: #151e2e;
            --card-inner: #1c273c;
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --accent-solar: #f59e0b;
            --accent-bat: #10b981;
            --accent-grid: #3b82f6;
            --accent-load: #ec4899;
            --border-color: rgba(255, 255, 255, 0.08);
            --danger: #ef4444;
        }
        * { -webkit-box-sizing: border-box; -moz-box-sizing: border-box; box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, "Open Sans", sans-serif;
            background-color: #0b0f19;
            background-color: var(--bg-color);
            color: #f8fafc;
            color: var(--text-main);
            padding: 16px;
            min-height: 100vh;
            -webkit-font-smoothing: antialiased;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        header {
            display: -webkit-box;
            display: -webkit-flex;
            display: -ms-flexbox;
            display: flex;
            -webkit-flex-wrap: wrap;
            -ms-flex-wrap: wrap;
            flex-wrap: wrap;
            -webkit-box-pack: justify;
            -webkit-justify-content: space-between;
            -ms-flex-pack: justify;
            justify-content: space-between;
            -webkit-box-align: center;
            -webkit-align-items: center;
            -ms-flex-align: center;
            align-items: center;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 12px;
            margin-bottom: 18px;
        }
        .header-title h1 {
            font-size: 1.4rem;
            font-weight: 700;
            color: #fbbf24;
        }
        .header-title .sub {
            font-size: 0.82rem;
            color: #94a3b8;
            color: var(--text-sub);
            margin-top: 3px;
        }
        .header-status {
            display: -webkit-box;
            display: -webkit-flex;
            display: -ms-flexbox;
            display: flex;
            -webkit-box-align: center;
            -webkit-align-items: center;
            -ms-flex-align: center;
            align-items: center;
            margin-top: 6px;
        }
        .badge {
            display: -webkit-inline-box;
            display: -webkit-inline-flex;
            display: -ms-inline-flexbox;
            display: inline-flex;
            -webkit-box-align: center;
            -webkit-align-items: center;
            -ms-flex-align: center;
            align-items: center;
            background: #1e293b;
            padding: 6px 14px;
            border-radius: 9999px;
            font-size: 0.85rem;
            font-weight: 500;
            color: #94a3b8;
            color: var(--text-sub);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border: 1px solid var(--border-color);
        }
        .dot {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            background-color: #10b981;
            box-shadow: 0 0 8px #10b981;
            display: inline-block;
            margin-right: 8px;
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
            50% { opacity: 0.4; -webkit-transform: scale(0.85); transform: scale(0.85); }
        }
        @keyframes pulse-dot {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(0.85); }
        }
        .flow-summary {
            background: #151e2e;
            background: var(--card-bg);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border: 1px solid var(--border-color);
            border-radius: 14px;
            padding: 14px 16px;
            margin-bottom: 16px;
            display: -webkit-box;
            display: -webkit-flex;
            display: -ms-flexbox;
            display: flex;
            -webkit-flex-wrap: wrap;
            -ms-flex-wrap: wrap;
            flex-wrap: wrap;
            -webkit-justify-content: space-around;
            -ms-flex-pack: distribute;
            justify-content: space-around;
            -webkit-box-align: center;
            -webkit-align-items: center;
            -ms-flex-align: center;
            align-items: center;
            text-align: center;
        }
        .flow-item {
            -webkit-box-flex: 1;
            -webkit-flex: 1 1 120px;
            -ms-flex: 1 1 120px;
            flex: 1 1 120px;
            margin: 4px;
        }
        .flow-label {
            font-size: 0.78rem;
            color: #94a3b8;
            color: var(--text-sub);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .flow-val {
            font-size: 1.25rem;
            font-weight: 700;
            margin-top: 3px;
        }
        .grid {
            display: -webkit-box;
            display: -webkit-flex;
            display: -ms-flexbox;
            display: flex;
            -webkit-flex-wrap: wrap;
            -ms-flex-wrap: wrap;
            flex-wrap: wrap;
            margin: -8px;
        }
        .card {
            -webkit-box-flex: 1;
            -webkit-flex: 1 1 calc(50% - 16px);
            -ms-flex: 1 1 calc(50% - 16px);
            flex: 1 1 calc(50% - 16px);
            min-width: 280px;
            margin: 8px;
            background: #151e2e;
            background: var(--card-bg);
            border-radius: 14px;
            padding: 16px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border: 1px solid var(--border-color);
            box-shadow: 0 8px 20px -6px rgba(0, 0, 0, 0.4);
            display: -webkit-box;
            display: -webkit-flex;
            display: -ms-flexbox;
            display: flex;
            -webkit-box-orient: vertical;
            -webkit-box-direction: normal;
            -webkit-flex-direction: column;
            -ms-flex-direction: column;
            flex-direction: column;
            -webkit-box-pack: justify;
            -webkit-justify-content: space-between;
            -ms-flex-pack: justify;
            justify-content: space-between;
            position: relative;
            overflow: hidden;
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
        .card-grid::before { background: #3b82f6; }
        .card-load::before { background: #ec4899; }

        .card-header {
            font-size: 1.1rem;
            font-weight: 700;
            display: -webkit-box;
            display: -webkit-flex;
            display: -ms-flexbox;
            display: flex;
            -webkit-box-pack: justify;
            -webkit-justify-content: space-between;
            -ms-flex-pack: justify;
            justify-content: space-between;
            -webkit-box-align: center;
            -webkit-align-items: center;
            -ms-flex-align: center;
            align-items: center;
            margin-bottom: 8px;
        }
        .metric-big {
            font-size: 2.2rem;
            font-weight: 800;
            margin: 6px 0 12px 0;
            letter-spacing: -0.5px;
            line-height: 1.1;
        }
        .solar-color { color: #f59e0b; color: var(--accent-solar); }
        .bat-color { color: #10b981; color: var(--accent-bat); }
        .grid-color { color: #3b82f6; color: var(--accent-grid); }
        .load-color { color: #ec4899; color: var(--accent-load); }

        .sub-metrics {
            margin-top: auto;
            font-size: 0.88rem;
            background: #1c273c;
            background: var(--card-inner);
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border: 1px solid var(--border-color);
        }
        .row {
            display: -webkit-box;
            display: -webkit-flex;
            display: -ms-flexbox;
            display: flex;
            -webkit-box-pack: justify;
            -webkit-justify-content: space-between;
            -ms-flex-pack: justify;
            justify-content: space-between;
            -webkit-box-align: center;
            -webkit-align-items: center;
            -ms-flex-align: center;
            align-items: center;
            padding: 5px 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }
        .row:last-child { border-bottom: none; }
        .row span:first-child { color: #94a3b8; color: var(--text-sub); }
        .val { color: #f8fafc; color: var(--text-main); font-weight: 600; }
        .val-badge {
            font-size: 0.78rem;
            padding: 3px 8px;
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.1);
        }
        footer {
            margin-top: 20px;
            text-align: center;
            font-size: 0.8rem;
            color: #94a3b8;
            color: var(--text-sub);
            padding-top: 10px;
        }
        @media (max-width: 600px) {
            body { padding: 10px; }
            .card { -webkit-flex-basis: 100%; -ms-flex-basis: 100%; flex-basis: 100%; margin: 6px 0; }
            .metric-big { font-size: 1.85rem; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-title">
                <h1>⚡ Deye SUN-12K Hybrid Inverter</h1>
                <div class="sub">Modbus TCP (Solarman V5) &bull; Live Telemetrie</div>
            </div>
            <div class="header-status">
                <div class="badge" id="status-badge">
                    <span class="dot pulse" id="status-dot"></span>
                    <span id="last-update">Verbinde...</span>
                </div>
            </div>
        </header>

        <!-- Power Summary Bar -->
        <div class="flow-summary">
            <div class="flow-item">
                <div class="flow-label">☀️ Erzeugung</div>
                <div class="flow-val solar-color" id="sum-solar">-- W</div>
            </div>
            <div class="flow-item">
                <div class="flow-label">🔋 Speicher</div>
                <div class="flow-val bat-color" id="sum-bat">-- %</div>
            </div>
            <div class="flow-item">
                <div class="flow-label">🔌 Netz</div>
                <div class="flow-val grid-color" id="sum-grid">-- W</div>
            </div>
            <div class="flow-item">
                <div class="flow-label">🏠 Verbrauch</div>
                <div class="flow-val load-color" id="sum-load">-- W</div>
            </div>
        </div>

        <div class="grid">
            <!-- PV Solar Card -->
            <div class="card card-solar">
                <div>
                    <div class="card-header solar-color">
                        <span>☀️ Solar PV</span>
                        <span class="val-badge" id="pv-today-badge">Heute: -- kWh</span>
                    </div>
                    <div class="metric-big solar-color" id="pv-total">-- W</div>
                </div>
                <div class="sub-metrics">
                    <div class="row"><span>PV String 1:</span><span class="val" id="pv1">--</span></div>
                    <div class="row"><span>PV String 2:</span><span class="val" id="pv2">--</span></div>
                    <div class="row"><span>Tagesertrag:</span><span class="val" id="pv-today">-- kWh</span></div>
                </div>
            </div>

            <!-- Battery Card -->
            <div class="card card-bat">
                <div>
                    <div class="card-header bat-color">
                        <span>🔋 Batterie</span>
                        <span class="val-badge" id="bat-status-badge">Lade...</span>
                    </div>
                    <div class="metric-big bat-color" id="bat-soc">-- %</div>
                </div>
                <div class="sub-metrics">
                    <div class="row"><span>Leistung:</span><span class="val" id="bat-power">-- W</span></div>
                    <div class="row"><span>Spannung / Strom:</span><span class="val" id="bat-vi">-- V / -- A</span></div>
                    <div class="row"><span>Batterietemperatur:</span><span class="val" id="bat-temp">-- °C</span></div>
                    <div class="row"><span>Ladung / Entladung:</span><span class="val" id="bat-today">-- / -- kWh</span></div>
                </div>
            </div>

            <!-- Grid Card -->
            <div class="card card-grid">
                <div>
                    <div class="card-header grid-color">
                        <span>🔌 Netzanschluss</span>
                        <span class="val-badge" id="grid-freq">-- Hz</span>
                    </div>
                    <div class="metric-big grid-color" id="grid-total">-- W</div>
                </div>
                <div class="sub-metrics">
                    <div class="row"><span>Phase L1:</span><span class="val" id="grid-l1">-- V / -- W</span></div>
                    <div class="row"><span>Phase L2:</span><span class="val" id="grid-l2">-- V / -- W</span></div>
                    <div class="row"><span>Phase L3:</span><span class="val" id="grid-l3">-- V / -- W</span></div>
                    <div class="row"><span>Kauf / Verkauf Heute:</span><span class="val" id="grid-today">--</span></div>
                </div>
            </div>

            <!-- Load Card -->
            <div class="card card-load">
                <div>
                    <div class="card-header load-color">
                        <span>🏠 Hausverbrauch</span>
                        <span class="val-badge" id="load-today-badge">Heute: -- kWh</span>
                    </div>
                    <div class="metric-big load-color" id="load-total">-- W</div>
                </div>
                <div class="sub-metrics">
                    <div class="row"><span>Phasen L1 / L2 / L3:</span><span class="val" id="load-phases">-- / -- / -- W</span></div>
                    <div class="row"><span>Tagesverbrauch:</span><span class="val" id="load-today">-- kWh</span></div>
                    <div class="row"><span>Inverter Temp (DC/AC):</span><span class="val" id="inv-temps">-- / -- °C</span></div>
                </div>
            </div>
        </div>

        <footer>
            <div id="footer-details">Deye SUN-12K &bull; Logger SN: ---</div>
        </footer>
    </div>

    <script>
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
            if (el) {
                el.innerText = text;
            }
        }

        function updateUI(d) {
            if (!d || typeof d !== 'object' || Object.keys(d).length === 0) {
                setElText('last-update', 'Warte auf Wechselrichter...');
                return;
            }

            // Status indicator
            var dot = document.getElementById('status-dot');
            if (dot) {
                dot.className = 'dot pulse';
            }
            var st = d.status_text || 'Normal';
            var timeStr = '';
            if (d.timestamp) {
                var parts = d.timestamp.split(' ');
                timeStr = parts.length > 1 ? parts[1] : d.timestamp;
            }
            setElText('last-update', (timeStr ? timeStr + ' ' : '') + '(' + st + ')');

            // Flow summary
            var pvTot = (typeof d.pv_power_total_w === 'number') ? d.pv_power_total_w : 0;
            var batPow = (typeof d.battery_power_w === 'number') ? d.battery_power_w : 0;
            var gridPow = (typeof d.grid_power_total_w === 'number') ? d.grid_power_total_w : 0;
            var loadTot = (typeof d.load_power_total_w === 'number') ? d.load_power_total_w : 0;

            setElText('sum-solar', fmtW(pvTot));
            setElText('sum-bat', (d.battery_soc_percent !== undefined ? d.battery_soc_percent : 0) + ' %');
            setElText('sum-grid', (gridPow >= 0 ? '+' : '') + fmtW(gridPow));
            setElText('sum-load', fmtW(loadTot));

            // PV Card
            setElText('pv-total', fmtW(pvTot));
            var pv1V = (typeof d.pv1_voltage_v === 'number') ? d.pv1_voltage_v.toFixed(1) : '0.0';
            var pv1I = (typeof d.pv1_current_a === 'number') ? d.pv1_current_a.toFixed(1) : '0.0';
            var pv1W = (typeof d.pv1_power_w === 'number') ? d.pv1_power_w.toFixed(0) : '0';
            setElText('pv1', pv1W + ' W (' + pv1V + ' V, ' + pv1I + ' A)');

            var pv2V = (typeof d.pv2_voltage_v === 'number') ? d.pv2_voltage_v.toFixed(1) : '0.0';
            var pv2I = (typeof d.pv2_current_a === 'number') ? d.pv2_current_a.toFixed(1) : '0.0';
            var pv2W = (typeof d.pv2_power_w === 'number') ? d.pv2_power_w.toFixed(0) : '0';
            setElText('pv2', pv2W + ' W (' + pv2V + ' V, ' + pv2I + ' A)');

            var pvToday = (typeof d.energy_pv_today_kwh === 'number') ? d.energy_pv_today_kwh.toFixed(1) : '0.0';
            setElText('pv-today', pvToday + ' kWh');
            setElText('pv-today-badge', 'Heute: ' + pvToday + ' kWh');

            // Battery Card
            var soc = (d.battery_soc_percent !== undefined) ? d.battery_soc_percent : 0;
            setElText('bat-soc', soc + ' %');
            var batStatusText = 'Standby';
            if (batPow > 20) {
                batStatusText = 'Laden (' + fmtW(batPow) + ')';
            } else if (batPow < -20) {
                batStatusText = 'Entladen (' + fmtW(Math.abs(batPow)) + ')';
            }
            setElText('bat-status-badge', batStatusText);
            setElText('bat-power', (batPow >= 0 ? '+' : '') + fmtW(batPow));
            var batV = (typeof d.battery_voltage_v === 'number') ? d.battery_voltage_v.toFixed(1) : '0.0';
            var batI = (typeof d.battery_current_a === 'number') ? d.battery_current_a.toFixed(1) : '0.0';
            setElText('bat-vi', batV + ' V / ' + batI + ' A');
            setElText('bat-temp', (d.temp_battery_celsius !== undefined ? d.temp_battery_celsius : 0) + ' \u00B0C');
            var batChgToday = (typeof d.energy_bat_charge_today_kwh === 'number') ? d.energy_bat_charge_today_kwh.toFixed(1) : '0.0';
            var batDisToday = (typeof d.energy_bat_dischg_today_kwh === 'number') ? d.energy_bat_dischg_today_kwh.toFixed(1) : '0.0';
            setElText('bat-today', batChgToday + ' / ' + batDisToday + ' kWh');

            // Grid Card
            setElText('grid-total', (gridPow >= 0 ? '+' : '') + fmtW(gridPow));
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

            // Load Card
            setElText('load-total', fmtW(loadTot));
            var lL1 = (typeof d.load_power_l1_w === 'number') ? d.load_power_l1_w : 0;
            var lL2 = (typeof d.load_power_l2_w === 'number') ? d.load_power_l2_w : 0;
            var lL3 = (typeof d.load_power_l3_w === 'number') ? d.load_power_l3_w : 0;
            setElText('load-phases', lL1 + ' / ' + lL2 + ' / ' + lL3 + ' W');
            var loadToday = (typeof d.energy_load_today_kwh === 'number') ? d.energy_load_today_kwh.toFixed(1) : '0.0';
            setElText('load-today', loadToday + ' kWh');
            setElText('load-today-badge', 'Heute: ' + loadToday + ' kWh');
            var dcTemp = (typeof d.temp_dc_celsius === 'number') ? d.temp_dc_celsius.toFixed(1) : '0';
            var acTemp = (typeof d.temp_ac_celsius === 'number') ? d.temp_ac_celsius.toFixed(1) : '0';
            setElText('inv-temps', dcTemp + ' \u00B0C / ' + acTemp + ' \u00B0C');

            // Footer
            if (d.logger_serial) {
                setElText('footer-details', 'Deye SUN-12K \u2022 Logger SN: ' + d.logger_serial + ' \u2022 ' + (d.timestamp || ''));
            }
        }

        function fetchMetrics() {
            try {
                var xhr = new XMLHttpRequest();
                xhr.open('GET', '/api/data?_t=' + (new Date().getTime()), true);
                xhr.timeout = 4000;
                xhr.onreadystatechange = function() {
                    if (xhr.readyState === 4) {
                        if (xhr.status >= 200 && xhr.status < 300) {
                            try {
                                var d = JSON.parse(xhr.responseText);
                                updateUI(d);
                            } catch (e) {
                                var lu = document.getElementById('last-update');
                                if (lu) lu.innerText = 'Datenfehler';
                            }
                        } else if (xhr.status !== 0) {
                            var dot = document.getElementById('status-dot');
                            if (dot) dot.className = 'dot err';
                            var lu = document.getElementById('last-update');
                            if (lu) lu.innerText = 'HTTP Fehler ' + xhr.status;
                        }
                    }
                };
                xhr.onerror = function() {
                    var dot = document.getElementById('status-dot');
                    if (dot) dot.className = 'dot err';
                    var lu = document.getElementById('last-update');
                    if (lu) lu.innerText = 'Verbindung unterbrochen';
                };
                xhr.ontimeout = function() {
                    var dot = document.getElementById('status-dot');
                    if (dot) dot.className = 'dot err';
                    var lu = document.getElementById('last-update');
                    if (lu) lu.innerText = 'Zeit\u00FCberschreitung';
                };
                xhr.send();
            } catch(e) {
                var dot = document.getElementById('status-dot');
                if (dot) dot.className = 'dot err';
                var lu = document.getElementById('last-update');
                if (lu) lu.innerText = 'Fehler';
            }
        }

        // Initialize immediately with server-injected data if available
        var initialData = /*__INITIAL_DATA__*/ null;
        if (initialData && typeof initialData === 'object' && initialData.status_code !== undefined) {
            updateUI(initialData);
        }

        // Fetch immediately and poll every 2.5 seconds
        fetchMetrics();
        setInterval(fetchMetrics, 2500);
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
