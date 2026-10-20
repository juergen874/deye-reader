<p align="center">
  <b><a href="#-english">🇬🇧 English</a></b> &nbsp;•&nbsp; <b><a href="#-deutsch">🇩🇪 Deutsch</a></b>
</p>

---

<a name="-english"></a>
# ☀️ Deye SUN-12K Modbus Reader & Dashboard

A lightweight, robust Python tool for querying telemetry and operating metrics from the **Deye SUN-12K-SG04LP3** (as well as compatible 3-phase Deye, Bosswerk, and Solark hybrid inverters) using the **Solarman V5 Protocol** (Modbus TCP Port 8899).

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Protocol SolarmanV5](https://img.shields.io/badge/protocol-Solarman%20V5%20%2F%20Modbus%20TCP-orange.svg)
![License MIT](https://img.shields.io/badge/license-MIT-green.svg)

---

## 🚀 Features

- 📊 **Comprehensive Telemetry**:
  - **PV Solar**: Power per string (PV1, PV2), voltage, current & daily generation (kWh).
  - **Battery Storage**: State of Charge (SOC %), charge/discharge power (W), voltage, current, temperature (°C) & daily charge/discharge energy.
  - **Grid**: Phase-specific voltage (L1, L2, L3) & active power, net feed-in / grid consumption, grid frequency (Hz), daily purchase & sale (kWh).
  - **Home Load**: Total consumption & per-phase load distribution (L1, L2, L3 W) plus daily load total (kWh).
  - **Inverter Health**: Operating status, DC & AC heatsink temperatures (°C).
- 📺 **Colorized Terminal Dashboard**: Integrated console dashboard with color highlighting and live polling (`--watch`).
- 🌐 **Built-in Web Server**: Standalone, responsive web dashboard (`--web 8080`) including a JSON REST API without external web frameworks.
- 🔍 **Auto-Discovery**: Automatic discovery of the Solarman data logger serial number via its HTTP web interface.
- 🤖 **JSON Export**: Direct output of structured JSON data (`--json`) for easy integration into Home Assistant, Node-RED, or cron automation.

---

## 🛠️ Compatible Inverters & Hardware

- **Deye SUN-12K-SG04LP3-EU** (3-phase low-voltage hybrid inverter)
- **Deye SUN-5K / 6K / 8K / 10K / 12K-SG04LP3**
- Rebranded OEM inverters (e.g., **Bosswerk**, **Solark 12K**)
- Data loggers: **Solarman LSW-3 / LNW-3 / Wi-Fi Stick V5**

---

## 📦 Installation

### 1. Clone the repository
```bash
git clone https://github.com/juergen874/deye-reader.git
cd deye-reader
```

### 2. Install dependencies
Requires `pysolarmanv5`:
```bash
pip install -r requirements.txt
```

---

## ⚡ Usage & Examples

### 1. Single reading in the terminal
```bash
python deye_reader.py --host 192.168.188.128
```

### 2. Live terminal dashboard (polling every 3 seconds)
```bash
python deye_reader.py --host 192.168.188.128 --watch 3
```

### 3. Start web dashboard & REST API
Launches a local HTTP server (default port `8080`):
```bash
python deye_reader.py --host 192.168.188.128 --web 8080
```
- 🌐 Web Interface: `http://localhost:8080`
- 🔌 JSON API Endpoint: `http://localhost:8080/api/data`

### 4. JSON output for scripts & automation
```bash
python deye_reader.py --host 192.168.188.128 --json
```

### 5. Explicit parameters (port & logger serial number)
If auto-discovery is disabled or not desired:
```bash
python deye_reader.py --host 192.168.188.128 --port 8899 --serial 1109501211
```

---

## ⚙️ CLI Options

| Option | Default | Description |
|---|---|---|
| `--host` | `192.168.188.128` | IP address of the Deye / Solarman logger |
| `--port` | `8899` | Modbus TCP port |
| `--slave-id` | `1` | Modbus slave ID / Unit ID |
| `--serial` | `1109501211` | Data logger serial number (auto-discovered if omitted) |
| `--watch [SECONDS]` | `3` | Continuous polling interval in seconds |
| `--json` | `false` | Output all metrics as a JSON object |
| `--web [PORT]` | `8080` | Starts the web dashboard on the specified port |

---

## 🗺️ Modbus Register Mapping

| Register | Field Name | Description | Unit |
|---|---|---|---|
| 500 | `status_code` | Inverter status (Standby, Normal, Alarm, Fault) | Code |
| 520 | `energy_grid_buy_today_kwh` | Energy bought from grid today | kWh |
| 521 | `energy_grid_sell_today_kwh` | Energy fed into grid today | kWh |
| 522 | `energy_bat_charge_today_kwh` | Battery charge energy today | kWh |
| 523 | `energy_bat_dischg_today_kwh` | Battery discharge energy today | kWh |
| 526 | `energy_load_today_kwh` | Home load energy today | kWh |
| 529 | `energy_pv_today_kwh` | PV solar yield today | kWh |
| 540, 541 | `temp_dc_celsius`, `temp_ac_celsius` | Heatsink temperature (DC / AC) | °C |
| 586 | `temp_battery_celsius` | Battery temperature | °C |
| 587 | `battery_voltage_v` | Battery voltage | V |
| 588 | `battery_soc_percent` | State of charge (SOC) | % |
| 589 | `battery_power_w` | Battery power (+ charging, - discharging) | W |
| 590 | `battery_current_a` | Battery current | A |
| 598 - 600 | `grid_voltage_l1_v` .. `l3_v` | Grid voltage phases L1, L2, L3 | V |
| 604 - 607 | `grid_power_l1_w` .. `total_w` | Grid active power phases L1, L2, L3 & total | W |
| 608 / 609 | `grid_frequency_hz` | Grid frequency | Hz |
| 625 - 627 | `inverter_power_l1_w` .. `l3_w` | Inverter output power L1, L2, L3 | W |
| 650 - 653 | `load_power_l1_w` .. `total_w` | Load power phases L1, L2, L3 & total | W |
| 676, 677 | `pv1_voltage_v`, `pv1_current_a` | PV String 1 voltage & current | V, A |
| 678, 679 | `pv2_voltage_v`, `pv2_current_a` | PV String 2 voltage & current | V, A |

---

## 📄 License

Published under the [MIT License](LICENSE).

---
---

<a name="-deutsch"></a>
# 🇩🇪 Deutsche Beschreibung

# ☀️ Deye SUN-12K Modbus Reader & Dashboard

Ein leichtgewichtiges, robustes Python-Skript zum Auslesen von Telemetrie- und Betriebsdaten des **Deye SUN-12K-SG04LP3** (sowie kompatiblen 3-Phasen Deye, Bosswerk und Solark Hybrid-Wechselrichtern) über das **Solarman V5 Protocol** (Modbus TCP Port 8899).

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Protocol SolarmanV5](https://img.shields.io/badge/protocol-Solarman%20V5%20%2F%20Modbus%20TCP-orange.svg)
![License MIT](https://img.shields.io/badge/license-MIT-green.svg)

---

## 🚀 Features

- 📊 **Umfassende Telemetrie**:
  - **PV Solar**: Leistung pro String (PV1, PV2), Spannung, Stromstärke & Tagesgesamtertrag (kWh).
  - **Batteriespeicher**: SOC (%), Lade-/Entladeleistung (W), Spannung, Stromstärke, Temperatur (°C) & Tages-Lademengen.
  - **Stromnetz (Grid)**: Phasenbezogene Spannung (L1, L2, L3) & Leistung, Gesamteinspeisung / Netzbezug, Netzfrequenz (Hz), Tageskauf & Tagesverkauf (kWh).
  - **Hausverbrauch (Load)**: Gesamtlast & Phasenverteilung (L1, L2, L3 W) sowie Tagesverbrauch (kWh).
  - **Inverter-Status**: Inverter-Betriebszustand, DC & AC Radiator-Temperaturen (°C).
- 📺 **Farbiges Terminal-Dashboard**: Integrierte Konsolenansicht mit Farbhervorhebung und Live-Polling (`--watch`).
- 🌐 **Integrierter Web-Server**: Eigenständiges, responsive Web-Dashboard (`--web 8080`) inkl. JSON REST API ohne externe Web-Frameworks.
- 🔍 **Auto-Discovery**: Automatische Erkennung der Solarman-Logger-Seriennummer über das HTTP-Webinterface des Datenloggers.
- 🤖 **JSON Export**: Direkte Ausgabe strukturierter Daten (`--json`) zur einfachen Integration in Home Assistant, Node-RED oder Cronjobs.

---

## 🛠️ Kompatible Wechselrichter & Hardware

- **Deye SUN-12K-SG04LP3-EU** (3-Phasen Niedervolt Hybrid)
- **Deye SUN-5K / 6K / 8K / 10K / 12K-SG04LP3**
- Baugleiche Wechselrichter (z. B. **Bosswerk**, **Solark 12K**)
- Datenlogger: **Solarman LSW-3 / LNW-3 / Wi-Fi Stick V5**

---

## 📦 Installation

### 1. Repository klonen
```bash
git clone https://github.com/juergen874/deye-reader.git
cd deye-reader
```

### 2. Abhängigkeiten installieren
Das Skript setzt `pysolarmanv5` voraus:
```bash
pip install -r requirements.txt
```

---

## ⚡ Nutzung & Beispiele

### 1. Einmaliges Auslesen im Terminal
```bash
python deye_reader.py --host 192.168.188.128
```

### 2. Live-Dashboard im Terminal (Polling alle 3 Sekunden)
```bash
python deye_reader.py --host 192.168.188.128 --watch 3
```

### 3. Web-Dashboard & REST API starten
Startet einen lokalen HTTP-Server (Standard-Port `8080`):
```bash
python deye_reader.py --host 192.168.188.128 --web 8080
```
- 🌐 Web Interface: `http://localhost:8080`
- 🔌 JSON API Endpoint: `http://localhost:8080/api/data`

### 4. JSON-Ausgabe für Skripte & Automatisierung
```bash
python deye_reader.py --host 192.168.188.128 --json
```

### 5. Manuelle Parameterübergabe (Port & Logger-Seriennummer)
Falls die automatische Ermittlung der Seriennummer nicht gewünscht ist:
```bash
python deye_reader.py --host 192.168.188.128 --port 8899 --serial 1109501211
```

---

## ⚙️ CLI Parameter

| Option | Standardwert | Beschreibung |
|---|---|---|
| `--host` | `192.168.188.128` | IP-Adresse des Deye / Solarman Datenloggers |
| `--port` | `8899` | Modbus TCP Port |
| `--slave-id` | `1` | Modbus Slave ID / Unit ID |
| `--serial` | `1109501211` | Seriennummer des Datenloggers (wird sonst auto-discovered) |
| `--watch [SEKUNDEN]` | `3` | Kontinuierliches Polling alle N Sekunden |
| `--json` | `false` | Ausgabe aller Messwerte als JSON-Objekt |
| `--web [PORT]` | `8080` | Startet das Web-Dashboard auf dem angegebenen Port |

---

## 🗺️ Ausgelesene Modbus-Register (Register-Mapping)

| Register | Feldname | Beschreibung | Einheit |
|---|---|---|---|
| 500 | `status_code` | Status des Wechselrichters (Standby, Normal, Alarm, Störung) | Code |
| 520 | `energy_grid_buy_today_kwh` | Netzbezug Heute | kWh |
| 521 | `energy_grid_sell_today_kwh` | Einspeisung Heute | kWh |
| 522 | `energy_bat_charge_today_kwh` | Batterieladung Heute | kWh |
| 523 | `energy_bat_dischg_today_kwh` | Batterieentladung Heute | kWh |
| 526 | `energy_load_today_kwh` | Hausverbrauch Heute | kWh |
| 529 | `energy_pv_today_kwh` | PV Ertrag Heute | kWh |
| 540, 541 | `temp_dc_celsius`, `temp_ac_celsius` | Kühlkörpertemperatur (DC / AC) | °C |
| 586 | `temp_battery_celsius` | Batterietemperatur | °C |
| 587 | `battery_voltage_v` | Batteriespannung | V |
| 588 | `battery_soc_percent` | Ladezustand Batterie (SOC) | % |
| 589 | `battery_power_w` | Batterie-Leistung (+ Laden, - Entladen) | W |
| 590 | `battery_current_a` | Batterie-Stromstärke | A |
| 598 - 600 | `grid_voltage_l1_v` .. `l3_v` | Netzspannung Phase L1, L2, L3 | V |
| 604 - 607 | `grid_power_l1_w` .. `total_w` | Netzleistung Phase L1, L2, L3 & Total | W |
| 608 / 609 | `grid_frequency_hz` | Netzfrequenz | Hz |
| 625 - 627 | `inverter_power_l1_w` .. `l3_w` | Wechselrichter-Ausgangsleistung L1, L2, L3 | W |
| 650 - 653 | `load_power_l1_w` .. `total_w` | Last / Hausverbrauch L1, L2, L3 & Total | W |
| 676, 677 | `pv1_voltage_v`, `pv1_current_a` | Solar String 1 Spannung & Strom | V, A |
| 678, 679 | `pv2_voltage_v`, `pv2_current_a` | Solar String 2 Spannung & Strom | V, A |

---

## 📄 Lizenz

Dieses Projekt ist unter der [MIT License](LICENSE) veröffentlicht.
