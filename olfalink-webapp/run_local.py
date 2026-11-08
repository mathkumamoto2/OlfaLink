"""Launch OlfaLink v1.9.2 locally: no GPU, Google Cloud or Firestore dependency.

Only the model and UI are shared with the Cloud Run version. All prediction
and demonstration results are labelled with their actual data provenance.
"""
from __future__ import annotations
import os
import time
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8765"


def read_local_config() -> dict[str, str]:
    path = ROOT / "config.env"
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in {"OLFALINK_READ_KEY", "OLFALINK_FORCE_DEMO"}:
            values[key] = value.strip().strip('"').strip("'")
    return values


def main() -> None:
    import torch
    import uvicorn
    from app.config import Settings
    from app.main import create_app
    from app.services.aws_live_data import AWSDataOnDemandStore
    from app.services.on_demand_store import MemoryOnDemandStore
    from app.services.combined_dashboard import get_model

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    get_model()  # Fail early if CPU model checkpoint is missing/corrupt.
    config = read_local_config()
    key = os.getenv("OLFALINK_READ_KEY") or config.get("OLFALINK_READ_KEY")
    if key and key.startswith("replace-"):
        key = None
    force_demo = (os.getenv("OLFALINK_FORCE_DEMO") or config.get("OLFALINK_FORCE_DEMO", "false")).lower() in {"true", "1", "yes"}
    if force_demo:
        key = None
    store = AWSDataOnDemandStore(key, MemoryOnDemandStore(), demo_fallback=True)
    app = create_app(Settings(app_mode="aws_pull", aws_read_key=key,
        llm_enabled=False, demo_fallback_enabled=True, live_device_id="esp1"), live_store=store)
    print(f"OlfaLink LOCAL v1.9.2: {URL}", flush=True)
    print("CPU CNN + dictionary. REAL AWS readings preferred unless FORCE_DEMO=true.", flush=True)
    print("When AWS is unavailable, clearly marked SYNTHETIC DEMO data will be used.", flush=True)
    print("Stop the app with Ctrl+C.", flush=True)
    if os.getenv("OLFALINK_NO_BROWSER", "0") != "1":
        timer = threading.Timer(2.0, lambda: webbrowser.open(URL))
        timer.daemon = True
        timer.start()
    uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
