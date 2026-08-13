#!/usr/bin/env python3
"""
Deye 12K Hybrid Inverter (SUN-12K-SG04LP3) Modbus Reader
Reads telemetry values from Deye SUN-12K over TCP/Solarman V5.
Default IP: 192.168.188.128 | Port: 8899 | Serial: Auto-discovered or 1109501211
"""

import socket
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
        if HAS_SOLARMAN:
            if not self.solarman_client:
                self._init_solarman()
            return self.solarman_client.read_holding_registers(register_addr=start_reg, quantity=count)
        else:
            raise RuntimeError("pysolarmanv5 library not installed. Run: pip install pysolarmanv5")

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
            
            freq_raw = b2[22] if len(b2) > 22 and b2[22] > 0 else (b2[23] if len(b2) > 23 else 2500)  # 608 / 609 (Grid Frequency)
            if freq_raw > 10000:
                freq_raw = 5000 + to_signed16(freq_raw)
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
        return data

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
    print(f"        Zeitstempel: {data.get('timestamp')} | Logger SN: {data.get('logger_serial')}")
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

latest_telemetry = {}

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Deye 12K Solar Dashboard</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --accent-solar: #f59e0b;
            --accent-bat: #10b981;
            --accent-grid: #3b82f6;
            --accent-load: #ec4899;
            --border-color: #334155;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 20px;
        }
        .container { max-width: 1100px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 15px;
            margin-bottom: 25px;
        }
        h1 { margin: 0; font-size: 1.6rem; color: #fbbf24; }
        .badge {
            background: #334155;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.85rem;
            color: var(--text-sub);
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
        }
        .card {
            background: var(--card-bg);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid var(--border-color);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
        }
        .card-header {
            font-size: 1.1rem;
            font-weight: bold;
            margin-bottom: 15px;
        }
        .metric-big {
            font-size: 2.2rem;
            font-weight: 800;
            margin: 10px 0;
        }
        .solar-color { color: var(--accent-solar); }
        .bat-color { color: var(--accent-bat); }
        .grid-color { color: var(--accent-grid); }
        .load-color { color: var(--accent-load); }
        .sub-metrics { margin-top: 15px; font-size: 0.9rem; color: var(--text-sub); }
        .row { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px dashed #334155; }
        .row:last-child { border-bottom: none; }
        .val { color: var(--text-main); font-weight: 600; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>Deye SUN-12K Hybrid Inverter</h1>
                <div style="font-size: 0.85rem; color: var(--text-sub);">Modbus TCP (Solarman V5) Telemetrie</div>
            </div>
            <div class="badge" id="last-update">Lade Daten...</div>
        </header>

        <div class="grid">
            <!-- PV Solar Card -->
            <div class="card">
                <div class="card-header solar-color">☀️ Solar PV Leistung</div>
                <div class="metric-big solar-color" id="pv-total">0 W</div>
                <div class="sub-metrics">
                    <div class="row"><span>PV String 1:</span><span class="val" id="pv1">0 W</span></div>
                    <div class="row"><span>PV String 2:</span><span class="val" id="pv2">0 W</span></div>
                    <div class="row"><span>PV Ertrag Heute:</span><span class="val" id="pv-today">0 kWh</span></div>
                </div>
            </div>

            <!-- Battery Card -->
            <div class="card">
                <div class="card-header bat-color">🔋 Batterie Speicher</div>
                <div class="metric-big bat-color" id="bat-soc">0 %</div>
                <div class="sub-metrics">
                    <div class="row"><span>Leistung:</span><span class="val" id="bat-power">0 W</span></div>
                    <div class="row"><span>Spannung / Strom:</span><span class="val" id="bat-vi">0 V / 0 A</span></div>
                    <div class="row"><span>Temperatur:</span><span class="val" id="bat-temp">0 °C</span></div>
                </div>
            </div>

            <!-- Grid Card -->
            <div class="card">
                <div class="card-header grid-color">🔌 Netzanschluss</div>
                <div class="metric-big grid-color" id="grid-total">0 W</div>
                <div class="sub-metrics">
                    <div class="row"><span>Phase L1:</span><span class="val" id="grid-l1">0 V / 0 W</span></div>
                    <div class="row"><span>Phase L2:</span><span class="val" id="grid-l2">0 V / 0 W</span></div>
                    <div class="row"><span>Phase L3:</span><span class="val" id="grid-l3">0 V / 0 W</span></div>
                    <div class="row"><span>Kauf / Verkauf Heute:</span><span class="val" id="grid-today">0 / 0 kWh</span></div>
                </div>
            </div>

            <!-- Load Card -->
            <div class="card">
                <div class="card-header load-color">🏠 Hausverbrauch</div>
                <div class="metric-big load-color" id="load-total">0 W</div>
                <div class="sub-metrics">
                    <div class="row"><span>Phase L1 / L2 / L3:</span><span class="val" id="load-phases">0 / 0 / 0 W</span></div>
                    <div class="row"><span>Verbrauch Heute:</span><span class="val" id="load-today">0 kWh</span></div>
                    <div class="row"><span>Inverter Temp (DC/AC):</span><span class="val" id="inv-temps">0 / 0 °C</span></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        async function fetchMetrics() {
            try {
                const res = await fetch('/api/data');
                const d = await res.json();
                
                document.getElementById('last-update').innerText = 'Stand: ' + (d.timestamp || 'N/A') + ' (' + (d.status_text || '') + ')';
                document.getElementById('pv-total').innerText = (d.pv_power_total_w || 0) + ' W';
                document.getElementById('pv1').innerText = (d.pv1_power_w || 0) + ' W (' + (d.pv1_voltage_v || 0) + 'V, ' + (d.pv1_current_a || 0) + 'A)';
                document.getElementById('pv2').innerText = (d.pv2_power_w || 0) + ' W (' + (d.pv2_voltage_v || 0) + 'V, ' + (d.pv2_current_a || 0) + 'A)';
                document.getElementById('pv-today').innerText = (d.energy_pv_today_kwh || 0) + ' kWh';

                document.getElementById('bat-soc').innerText = (d.battery_soc_percent || 0) + ' %';
                document.getElementById('bat-power').innerText = (d.battery_power_w || 0) + ' W';
                document.getElementById('bat-vi').innerText = (d.battery_voltage_v || 0) + ' V / ' + (d.battery_current_a || 0) + ' A';
                document.getElementById('bat-temp').innerText = (d.temp_battery_celsius || 0) + ' °C';

                document.getElementById('grid-total').innerText = (d.grid_power_total_w || 0) + ' W';
                document.getElementById('grid-l1').innerText = (d.grid_voltage_l1_v || 0) + 'V / ' + (d.grid_power_l1_w || 0) + 'W';
                document.getElementById('grid-l2').innerText = (d.grid_voltage_l2_v || 0) + 'V / ' + (d.grid_power_l2_w || 0) + 'W';
                document.getElementById('grid-l3').innerText = (d.grid_voltage_l3_v || 0) + 'V / ' + (d.grid_power_l3_w || 0) + 'W';
                document.getElementById('grid-today').innerText = (d.energy_grid_buy_today_kwh || 0) + ' / ' + (d.energy_grid_sell_today_kwh || 0) + ' kWh';

                document.getElementById('load-total').innerText = (d.load_power_total_w || 0) + ' W';
                document.getElementById('load-phases').innerText = (d.load_power_l1_w || 0) + ' / ' + (d.load_power_l2_w || 0) + ' / ' + (d.load_power_l3_w || 0) + ' W';
                document.getElementById('load-today').innerText = (d.energy_load_today_kwh || 0) + ' kWh';
                document.getElementById('inv-temps').innerText = (d.temp_dc_celsius || 0) + '°C / ' + (d.temp_ac_celsius || 0) + '°C';
            } catch(e) {
                console.error(e);
            }
        }
        fetchMetrics();
        setInterval(fetchMetrics, 3000);
    </script>
</body>
</html>
"""

class WebDashboardServer(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(latest_telemetry).encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
            
    def log_message(self, format, *args):
        pass

def run_web_server(port: int):
    server = HTTPServer(("0.0.0.0", port), WebDashboardServer)
    server.serve_forever()

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
