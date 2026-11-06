"""OlfaLink ESP32 v8 serial monitor -> locally saved CSV.

Windows usage:
    py -m pip install pyserial
    py olfalink_usb_logger.py --list
    py olfalink_usb_logger.py --port COM5 --session vinegar_001

Close Arduino IDE Serial Monitor first. Logs are PC-local only; no cloud/server.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial がありません。PowerShellで py -m pip install pyserial を実行してください。")

HEADER = [
    "captured_at_pc", "session_id", "device_id", "sample_number", "stage",
    "bme_status", "ads_status", "sgp_status",
    "tgs2602_adc", "tgs2602_vout_v", "tgs2602_rs_kohm",
    "tgs2603_adc", "tgs2603_vout_v", "tgs2603_rs_kohm",
    "bme_temp_c", "bme_rh_pct", "bme_pressure_hpa", "bme_gas_kohm",
    "sgp_voc_raw", "sgp_nox_raw", "sgp_rh_compensation",
    "sgp_voc_index", "sgp_nox_index",
]

SAMPLE = re.compile(r"\[SAMPLE\]\s*#(?P<sample_number>\d+)\s+stage=(?P<stage>\w+)\s+BME=(?P<bme_status>OK|--)\s+ADS=(?P<ads_status>OK|--)\s+SGP=(?P<sgp_status>OK|--)")
TGS = re.compile(r"TGS260(?P<channel>[23])\s+A[01]:\s+ADC=(?P<adc>-?\d+)\s+Vout=(?P<vout>-?\d+(?:\.\d+)?)V\s+Rs=(?P<rs>-?\d+(?:\.\d+)?)kOhm")
BME = re.compile(r"BME688:\s+T=(?P<temp>-?\d+(?:\.\d+)?)C\s+RH=(?P<rh>-?\d+(?:\.\d+)?)%\s+P=(?P<pressure>-?\d+(?:\.\d+)?)hPa\s+Gas=(?P<gas>-?\d+(?:\.\d+)?)kOhm")
SGP = re.compile(r"SGP41:\s+VOCraw=(?P<voc>\d+)\s+NOxraw=(?P<nox>\d+)\s+\(RH compensation=(?P<compensation>\w+)\)")
INDEX = re.compile(r"Sensirion Index:\s+VOC=(?P<voc>-?\d+)\s+NOx=(?P<nox>-?\d+)")


def update_row(row: dict[str, str] | None, line: str) -> dict[str, str] | None:
    """Parse one of the known v8 serial lines without fabricating sensor values."""
    sample = SAMPLE.search(line)
    if sample:
        return None  # The caller starts a new row separately.
    if row is None:
        return None
    if m := TGS.search(line):
        label = "tgs2602" if m["channel"] == "2" else "tgs2603"
        row[f"{label}_adc"] = m["adc"]
        row[f"{label}_vout_v"] = m["vout"]
        row[f"{label}_rs_kohm"] = m["rs"]
    elif m := BME.search(line):
        row.update(bme_temp_c=m["temp"], bme_rh_pct=m["rh"],
                   bme_pressure_hpa=m["pressure"], bme_gas_kohm=m["gas"])
    elif m := SGP.search(line):
        row.update(sgp_voc_raw=m["voc"], sgp_nox_raw=m["nox"],
                   sgp_rh_compensation=m["compensation"])
    elif m := INDEX.search(line):
        row.update(sgp_voc_index=m["voc"], sgp_nox_index=m["nox"])
    return row


def unique_filename(folder: Path, session: str) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = folder / f"olfalink_{session}_{stamp}.csv"
    number = 2
    while base.exists():
        base = folder / f"olfalink_{session}_{stamp}_{number}.csv"
        number += 1
    return base


def main() -> None:
    parser = argparse.ArgumentParser(description="Save OlfaLink v8 serial measurements directly to a PC-local CSV")
    parser.add_argument("--list", action="store_true", help="Show detected serial ports")
    parser.add_argument("--port", help="ESP32 serial port, e.g. COM5")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--session", default="room_001", help="Experiment identifier included in rows/filename")
    parser.add_argument("--device", default="esp1")
    parser.add_argument("--out-dir", type=Path, default=Path("measurements"))
    args = parser.parse_args()

    if args.list:
        ports = list(list_ports.comports())
        for p in ports:
            print(f"{p.device}: {p.description}")
        if not ports:
            print("ポートが見つかりません。USBケーブルとドライバを確認してください。")
        return
    if not args.port:
        parser.error("--port COM5 のように、ESP32のCOM番号を指定してください。まず --list で確認できます。")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.session):
        parser.error("--session は英数字・_・- のみ（最大64文字）")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = unique_filename(args.out_dir, args.session)
    raw_path = csv_path.with_suffix(".log")

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.2)
    except serial.SerialException as exc:
        sys.exit(f"{args.port} を開けません: {exc}\nArduino IDEのシリアルモニタを閉じてください。")

    with port, csv_path.open("x", newline="", encoding="utf-8-sig") as f_csv, raw_path.open("x", encoding="utf-8") as f_log:
        writer = csv.DictWriter(f_csv, fieldnames=HEADER)
        writer.writeheader()
        row = None
        count = 0
        print(f"[SAVE] CSV: {csv_path.resolve()}")
        print(f"[SAVE] RAW: {raw_path.resolve()}")
        print("[CONTROL] b=baseline / e=exposure / r=refresh / i=idle, Ctrl+C=終了")
        print("[NOTE] Arduino IDEのシリアルモニタと同時には使えません。")
        try:
            while True:
                # On Windows, keys are forwarded to ESP32 while receiving telemetry.
                if sys.platform == "win32":
                    import msvcrt
                    while msvcrt.kbhit():
                        key = msvcrt.getwch().lower()
                        if key in "beri":
                            port.write(key.encode("ascii"))
                            print(f"\n[CONTROL] Sent {key}")
                data = port.readline()
                if not data:
                    continue
                line = data.decode("utf-8", errors="replace").rstrip("\r\n")
                f_log.write(line + "\n")
                f_log.flush()
                if sample := SAMPLE.search(line):
                    if row is not None:
                        writer.writerow(row)
                        f_csv.flush()
                        count += 1
                        print(f"\r[SAVED] {count} sample(s)", end="", flush=True)
                    now = dt.datetime.now().astimezone().isoformat(timespec="milliseconds")
                    row = {field: "" for field in HEADER}
                    row.update({"captured_at_pc": now, "session_id": args.session, "device_id": args.device, **sample.groupdict()})
                else:
                    row = update_row(row, line)
        except KeyboardInterrupt:
            print("\n[STOP] Logging stopped")
        finally:
            if row is not None:
                writer.writerow(row)
                f_csv.flush()
                count += 1
            print(f"[DONE] {count} rows written to {csv_path}")


if __name__ == "__main__":
    main()
