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
