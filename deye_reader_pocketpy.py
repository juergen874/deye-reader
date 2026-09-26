#!/usr/bin/env python3
# ==============================================================================
# Deye SUN-12K Hybrid Inverter Modbus & Solarman V5 Reader for PocketPy / Python
# Standalone, ultra-lightweight: ZERO external dependencies (no pysolarmanv5/umodbus)
# Compatible with PocketPy C11 engine, PocketPy IDE (Android) and standard Python 3
# ==============================================================================

import sys
import time

try:
    import socket
except ImportError:
    socket = None

def to_signed16(val):
    """Convert unsigned 16-bit integer (0..65535) to signed (-32768..32767)."""
    return val if val < 0x8000 else val - 0x10000

def modbus_crc16(data):
    """Calculate Modbus RTU CRC16 (polynomial 0xA001). Returns [crc_low, crc_high]."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return [crc & 0xFF, (crc >> 8) & 0xFF]

def v5_checksum(frame):
    """Calculate Solarman V5 frame checksum (sum of bytes 1 to N-2, modulo 256)."""
    checksum = 0
    for i in range(1, len(frame) - 2):
        checksum += frame[i] & 0xFF
    return checksum & 0xFF

class PocketSolarmanV5:
    """Ultra-lightweight Solarman V5 protocol implementation."""
    def __init__(self, host="192.168.188.128", port=8899, serial_number=1109501211, slave_id=1, timeout=4.0):
        self.host = host
        self.port = port
        self.serial_number = serial_number
        self.slave_id = slave_id
        self.timeout = timeout
        self.sock = None
        self.seq = 1

    def connect(self):
        if socket is None:
            raise RuntimeError("Socket module not available in this environment.")
        self.sock = socket.socket()
        if hasattr(self.sock, "settimeout"):
            self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def build_frame(self, start_reg, count):
        # 1. Build Modbus RTU Request: SlaveID, FC03 (Read Holding Regs), StartReg, Count
        mb_req = [
            self.slave_id,
            3,
            (start_reg >> 8) & 0xFF,
            start_reg & 0xFF,
            (count >> 8) & 0xFF,
            count & 0xFF
        ]
        mb_req.extend(modbus_crc16(mb_req))

        # 2. Wrap in Solarman V5 frame
        payload_len = 15 + len(mb_req)
        frame = [
            0xA5,                                       # Start Byte
            payload_len & 0xFF, (payload_len >> 8) & 0xFF, # Length (little-endian)
            0x10, 0x45,                                 # Control Code 0x4510
            self.seq & 0xFF, 0x00,                      # Sequence number
            self.serial_number & 0xFF,                  # Logger Serial (4 bytes, little-endian)
            (self.serial_number >> 8) & 0xFF,
            (self.serial_number >> 16) & 0xFF,
            (self.serial_number >> 24) & 0xFF,
            0x02,                                       # Frame type: Modbus RTU
            0x00, 0x00,                                 # Sensor type
            0x00, 0x00, 0x00, 0x00,                     # Delivery time
            0x00, 0x00, 0x00, 0x00,                     # Power on time
            0x00, 0x00, 0x00, 0x00                      # Offset time
        ]
        frame.extend(mb_req)
        frame.append(0)     # Checksum placeholder
        frame.append(0x15)  # End Byte

        frame[-2] = v5_checksum(frame)
        self.seq = (self.seq + 1) & 0xFF
        return bytes(frame)

    def parse_response(self, raw_bytes):
        if len(raw_bytes) < 28 or raw_bytes[0] != 0xA5 or raw_bytes[-1] != 0x15:
            raise ValueError("Ungültiger Solarman V5 Frame-Header oder Footer.")

        # Modbus RTU Antwort beginnt ab Offset 25:
        # [SlaveID, FC03, ByteCount, Reg0_Hi, Reg0_Lo, ..., CRC_Lo, CRC_Hi]
        modbus_payload = raw_bytes[25:-2]
        if len(modbus_payload) < 5:
            raise ValueError("Modbus-Nutzlast zu kurz.")

        byte_count = modbus_payload[2]
        registers = []
        for i in range(0, byte_count, 2):
            val = (modbus_payload[3 + i] << 8) | modbus_payload[4 + i]
            registers.append(val)
        return registers

    def read_holding_registers(self, start_reg, count):
        req_frame = self.build_frame(start_reg, count)
        if not self.sock:
            self.connect()

        self.sock.send(req_frame)
        buf = self.sock.recv(1024)
        if not buf:
            raise RuntimeError("Keine Antwort vom Wechselrichter empfangen.")

        while len(buf) < 3:
            chunk = self.sock.recv(1024)
            if not chunk:
                break
            buf = buf + chunk

        payload_len = buf[1] | (buf[2] << 8)
        expected_len = 13 + payload_len

        while len(buf) < expected_len:
            chunk = self.sock.recv(expected_len - len(buf))
            if not chunk:
                break
            buf = buf + chunk

        return self.parse_response(buf)

class DeyeReader:
    """High-level Deye SUN-12K Inverter Reader and Decoder."""
    def __init__(self, host="192.168.188.128", port=8899, serial=1109501211, slave_id=1, timeout=4.0):
        self.host = host
        self.port = port
        self.serial = serial
        self.slave_id = slave_id
        self.timeout = timeout
        self.client = None

    def read_telemetry(self):
        """Reads live telemetry from inverter. Falls back to mock on error if offline."""
        data = {}
        try:
            if not self.client:
                self.client = PocketSolarmanV5(self.host, self.port, self.serial, self.slave_id, self.timeout)

            # Block 1: 500 to 541 (42 Register)
            b1 = self.client.read_holding_registers(500, 42)
            status_map = {0: "Standby", 1: "Self-Test", 2: "Normal (Hybrid/Netzeinspeisung)", 3: "Alarm", 4: "Störung"}
            data["status_code"] = b1[0]
            data["status_text"] = status_map.get(b1[0], f"Status {b1[0]}")
            data["energy_grid_buy_today_kwh"] = round(b1[20] * 0.1, 2)
            data["energy_grid_sell_today_kwh"] = round(b1[21] * 0.1, 2)
            data["energy_bat_charge_today_kwh"] = round(b1[22] * 0.1, 2)
            data["energy_bat_dischg_today_kwh"] = round(b1[23] * 0.1, 2)
            data["energy_load_today_kwh"] = round(b1[26] * 0.1, 2)
            data["energy_pv_today_kwh"] = round(b1[29] * 0.1, 2)
            data["temp_dc_celsius"] = round((b1[40] - 1000) * 0.1, 1) if b1[40] >= 1000 else round(b1[40] * 0.1 - 100, 1)
            data["temp_ac_celsius"] = round((b1[41] - 1000) * 0.1, 1) if b1[41] >= 1000 else round(b1[41] * 0.1 - 100, 1)

            # Block 2: 586 to 612 (27 Register)
            b2 = self.client.read_holding_registers(586, 27)
            raw_bat_temp = b2[0]
            data["temp_battery_celsius"] = round((raw_bat_temp - 1000) * 0.1, 1) if raw_bat_temp >= 1000 else round(raw_bat_temp * 0.1 - 100, 1)
            data["battery_voltage_v"] = round(b2[1] * 0.01, 2)
            data["battery_soc_percent"] = b2[2]
            data["battery_power_w"] = to_signed16(b2[3])
            data["battery_current_a"] = round(to_signed16(b2[4]) * 0.02, 2)

            data["grid_voltage_l1_v"] = round(b2[12] * 0.1, 1)
            data["grid_voltage_l2_v"] = round(b2[13] * 0.1, 1)
            data["grid_voltage_l3_v"] = round(b2[14] * 0.1, 1)
            data["grid_power_l1_w"] = to_signed16(b2[18])
            data["grid_power_l2_w"] = to_signed16(b2[19])
            data["grid_power_l3_w"] = to_signed16(b2[20])
            data["grid_power_total_w"] = to_signed16(b2[21])

            freq_raw = b2[22] if len(b2) > 22 and b2[22] > 0 else 5000
            data["grid_frequency_hz"] = round(freq_raw * 0.01, 2) if freq_raw > 1000 else round(freq_raw * 0.1, 2)

            # Block 3: 625 to 653 (29 Register)
            b3 = self.client.read_holding_registers(625, 29)
            data["inverter_power_l1_w"] = to_signed16(b3[0])
            data["inverter_power_l2_w"] = to_signed16(b3[1])
            data["inverter_power_l3_w"] = to_signed16(b3[2])
            data["inverter_power_total_w"] = to_signed16(b3[11])
            data["load_power_l1_w"] = to_signed16(b3[25])
            data["load_power_l2_w"] = to_signed16(b3[26])
            data["load_power_l3_w"] = to_signed16(b3[27])
            data["load_power_total_w"] = to_signed16(b3[28])

            # Block 4: 672 to 679 (8 Register)
            b4 = self.client.read_holding_registers(672, 8)
            data["pv1_voltage_v"] = round(b4[4] * 0.1, 1)
            data["pv1_current_a"] = round(b4[5] * 0.1, 1)
            data["pv1_power_w"] = round(data["pv1_voltage_v"] * data["pv1_current_a"], 1)
            data["pv2_voltage_v"] = round(b4[6] * 0.1, 1)
            data["pv2_current_a"] = round(b4[7] * 0.1, 1)
            data["pv2_power_w"] = round(data["pv2_voltage_v"] * data["pv2_current_a"], 1)
            data["pv_power_total_w"] = round(data["pv1_power_w"] + data["pv2_power_w"], 1)

            data["logger_serial"] = self.serial
            data["online"] = True
            data["mode"] = "LIVE (Modbus TCP)"
            return data

        except Exception as e:
            if self.client:
                self.client.close()
                self.client = None
            raise e

def render_terminal_dashboard(data):
    """Outputs an ANSI-color formatted dashboard directly in the PocketPy terminal."""
    C_RESET = "\033[0m"
    C_BOLD = "\033[1m"
    C_GREEN = "\033[32m"
    C_YELLOW = "\033[33m"
    C_BLUE = "\033[34m"
    C_CYAN = "\033[36m"
    C_RED = "\033[31m"

    mode_label = data.get("mode", "LIVE")
    print(f"{C_BOLD}{C_CYAN}========================================================================{C_RESET}")
    print(f"{C_BOLD}{C_YELLOW}        DEYE SUN-12K-SG04LP3 MODBUS TELEMETRIE ({mode_label}){C_RESET}")
    print(f"        Logger SN: {data.get('logger_serial', 'N/A')}")
    print(f"{C_BOLD}{C_CYAN}========================================================================{C_RESET}")

    st = data.get("status_text", "N/A")
    st_color = C_GREEN if "Normal" in st else C_YELLOW
    print(f"{C_BOLD}Status:{C_RESET} {st_color}{st}{C_RESET}")
    print("------------------------------------------------------------------------")

    pv_total = data.get("pv_power_total_w", 0)
    print(f"{C_BOLD}{C_GREEN}☀️  SOLAR / PV (Gesamt: {pv_total:.1f} W | Heute: {data.get('energy_pv_today_kwh', 0)} kWh){C_RESET}")
    print(f"   String 1: {data.get('pv1_power_w', 0):.1f} W  ({data.get('pv1_voltage_v', 0):.1f} V, {data.get('pv1_current_a', 0):.1f} A)")
    print(f"   String 2: {data.get('pv2_power_w', 0):.1f} W  ({data.get('pv2_voltage_v', 0):.1f} V, {data.get('pv2_current_a', 0):.1f} A)")
    print("------------------------------------------------------------------------")

    bat_pow = data.get("battery_power_w", 0)
    bat_soc = data.get("battery_soc_percent", 0)
    bat_status = "Laden" if bat_pow > 0 else ("Entladen" if bat_pow < 0 else "Standby")
    bat_color = C_GREEN if bat_pow >= 0 else C_YELLOW
    print(f"{C_BOLD}{C_CYAN}🔋 BATTERIE ({bat_soc}% | {bat_color}{bat_status} {abs(bat_pow)} W{C_RESET}){C_RESET}")
    print(f"   Spannung: {data.get('battery_voltage_v', 0):.2f} V | Strom: {data.get('battery_current_a', 0):.2f} A | Temp: {data.get('temp_battery_celsius', 0)} °C")
    print("------------------------------------------------------------------------")

    grid_pow = data.get("grid_power_total_w", 0)
    grid_status = f"Bezug ({grid_pow} W)" if grid_pow >= 0 else f"Einspeisung ({abs(grid_pow)} W)"
    grid_color = C_RED if grid_pow > 0 else C_GREEN
    print(f"{C_BOLD}{C_BLUE}🔌 NETZ (Grid: {grid_color}{grid_status}{C_RESET} | {data.get('grid_frequency_hz', 0)} Hz){C_RESET}")
    print(f"   Phase L1: {data.get('grid_voltage_l1_v', 0):.1f} V | {data.get('grid_power_l1_w', 0)} W")
    print(f"   Phase L2: {data.get('grid_voltage_l2_v', 0):.1f} V | {data.get('grid_power_l2_w', 0)} W")
    print(f"   Phase L3: {data.get('grid_voltage_l3_v', 0):.1f} V | {data.get('grid_power_l3_w', 0)} W")
    print(f"   Kauf Heute: {data.get('energy_grid_buy_today_kwh', 0)} kWh | Verkauf Heute: {data.get('energy_grid_sell_today_kwh', 0)} kWh")
    print("------------------------------------------------------------------------")

    load_pow = data.get("load_power_total_w", 0)
    print(f"{C_BOLD}{C_YELLOW}🏠 HAUSVERBRAUCH (Gesamt: {load_pow} W | Heute: {data.get('energy_load_today_kwh', 0)} kWh){C_RESET}")
    print(f"   Phase L1: {data.get('load_power_l1_w', 0)} W")
    print(f"   Phase L2: {data.get('load_power_l2_w', 0)} W")
    print(f"   Phase L3: {data.get('load_power_l3_w', 0)} W")
    print("------------------------------------------------------------------------")

    print(f"🌡️  Temperatur Inverter: DC Radiator: {data.get('temp_dc_celsius', 0)} °C | AC Radiator: {data.get('temp_ac_celsius', 0)} °C")
    print(f"{C_BOLD}{C_CYAN}========================================================================{C_RESET}")

def write_html_dashboard(data, filename="deye_dashboard.html"):
    """Writes an interactive SVG/HTML5 dashboard file for PocketPy IDE Visual WebView."""
    bat_pow = data.get("battery_power_w", 0)
    bat_soc = data.get("battery_soc_percent", 0)
    grid_pow = data.get("grid_power_total_w", 0)
    pv_total = data.get("pv_power_total_w", 0)
    load_pow = data.get("load_power_total_w", 0)

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Deye 12K Solar Dashboard</title>
  <style>
    body {{ background: #060913; color: #f8fafc; font-family: -apple-system, sans-serif; padding: 16px; margin: 0; }}
    .header {{ text-align: center; border-bottom: 1px solid #1e293b; padding-bottom: 12px; margin-bottom: 16px; }}
    .header h1 {{ font-size: 1.4rem; color: #fbbf24; margin: 0 0 4px 0; }}
    .header .meta {{ font-size: 0.8rem; color: #94a3b8; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
    .card {{ background: #0d1527; border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 14px; box-shadow: 0 4px 12px rgba(0,0,0,0.4); }}
    .card-title {{ font-size: 0.85rem; text-transform: uppercase; color: #94a3b8; font-weight: bold; margin-bottom: 6px; }}
    .card-val {{ font-size: 2rem; font-weight: 800; }}
    .solar {{ color: #f59e0b; border-top: 3px solid #f59e0b; }}
    .battery {{ color: #10b981; border-top: 3px solid #10b981; }}
    .grid-card {{ color: #38bdf8; border-top: 3px solid #38bdf8; }}
    .load {{ color: #ec4899; border-top: 3px solid #ec4899; }}
    .gauge {{ width: 100%; height: 8px; background: rgba(255,255,255,0.1); border-radius: 4px; overflow: hidden; margin: 8px 0; }}
    .gauge-fill {{ height: 100%; width: {bat_soc}%; background: #10b981; }}
    .sub {{ font-size: 0.8rem; color: #94a3b8; margin-top: 8px; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 6px; }}
    .sub-row {{ display: flex; justify-content: space-between; padding: 2px 0; }}
  </style>
</head>
<body>
  <div class="header">
    <h1>⚡ Deye SUN-12K Hybrid Inverter</h1>
    <div class="meta">Status: {data.get('status_text')} &bull; SN: {data.get('logger_serial')} &bull; PocketPy Engine</div>
  </div>

  <div class="grid">
    <div class="card solar">
      <div class="card-title">☀️ Solar / Photovoltaik</div>
      <div class="card-val">{pv_total:.1f} W</div>
      <div class="sub">
        <div class="sub-row"><span>Heute:</span> <b>{data.get('energy_pv_today_kwh')} kWh</b></div>
        <div class="sub-row"><span>PV1:</span> <span>{data.get('pv1_power_w')} W ({data.get('pv1_voltage_v')} V)</span></div>
        <div class="sub-row"><span>PV2:</span> <span>{data.get('pv2_power_w')} W ({data.get('pv2_voltage_v')} V)</span></div>
      </div>
    </div>

    <div class="card battery">
      <div class="card-title">🔋 Batteriespeicher</div>
      <div class="card-val">{bat_soc} %</div>
      <div class="gauge"><div class="gauge-fill"></div></div>
      <div class="sub">
        <div class="sub-row"><span>Leistung:</span> <b>{bat_pow} W</b></div>
        <div class="sub-row"><span>Spannung:</span> <span>{data.get('battery_voltage_v')} V ({data.get('battery_current_a')} A)</span></div>
        <div class="sub-row"><span>Temperatur:</span> <span>{data.get('temp_battery_celsius')} °C</span></div>
      </div>
    </div>

    <div class="card grid-card">
      <div class="card-title">🔌 Stromnetz</div>
      <div class="card-val">{grid_pow} W</div>
      <div class="sub">
        <div class="sub-row"><span>Kauf Heute:</span> <b>{data.get('energy_grid_buy_today_kwh')} kWh</b></div>
        <div class="sub-row"><span>Verkauf Heute:</span> <b>{data.get('energy_grid_sell_today_kwh')} kWh</b></div>
        <div class="sub-row"><span>Netzfrequenz:</span> <span>{data.get('grid_frequency_hz')} Hz</span></div>
      </div>
    </div>

    <div class="card load">
      <div class="card-title">🏠 Hausverbrauch</div>
      <div class="card-val">{load_pow} W</div>
      <div class="sub">
        <div class="sub-row"><span>Heute:</span> <b>{data.get('energy_load_today_kwh')} kWh</b></div>
        <div class="sub-row"><span>Phase L1:</span> <span>{data.get('load_power_l1_w')} W</span></div>
        <div class="sub-row"><span>Phase L2:</span> <span>{data.get('load_power_l2_w')} W</span></div>
        <div class="sub-row"><span>Phase L3:</span> <span>{data.get('load_power_l3_w')} W</span></div>
      </div>
    </div>
  </div>
</body>
</html>
"""
    try:
        with open(filename, "w") as f:
            f.write(html)
        print(f"\n🌐 Dashboard HTML aktualisiert: {filename} (in PocketPy IDE Visual-Tab öffnen)")
    except Exception as e:
        print(f"Konnte {filename} nicht schreiben: {e}")

def main():
    # Standard-Konfiguration des Deye-Wechselrichters
    HOST = "192.168.188.128"
    PORT = 8899
    SERIAL = 1109501211

    reader = DeyeReader(host=HOST, port=PORT, serial=SERIAL)
    print(f"Verbinde zu Deye Inverter ({HOST}:{PORT})...")
    data = reader.read_telemetry()
    render_terminal_dashboard(data)
    write_html_dashboard(data, "deye_dashboard.html")

if __name__ == "__main__":
    main()
