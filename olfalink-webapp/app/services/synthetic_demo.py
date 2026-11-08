"""Generate clearly identified SYNTHETIC 90-second sensor windows for demos.

No values from this module are real measurements, and none are ground-truth.
Only used after the real AWS measurement fetch/validation fails (when enabled).
"""
from __future__ import annotations

import math
import random
import secrets
import time


def synthetic_window(device_id: str, *, now_ms: int | None = None) -> tuple[list[dict], str, int]:
    """91 samples at 1 Hz, all seven channels, with bounded random variation.

    Generates physically plausible-looking values ONLY for UI demonstration.
    They are NOT calibrated sensor simulations or verified odor responses.
    """
    end = int(time.time() * 1000) if now_ms is None else now_ms
    rng = random.Random(secrets.randbits(128))
    # The second/third slots provide variation without a fixed trained label.
    response = rng.uniform(-0.16, 0.24)
    noise = rng.uniform(0.004, 0.015)
    bases = {
        "bme_gas_ohm": rng.uniform(9_500, 18_000),
        "bme_t": rng.uniform(21.0, 26.0),
        "bme_rh": rng.uniform(42.0, 60.0),
        "tgs2602_adc": rng.uniform(650, 1700),
        "tgs2603_adc": rng.uniform(600, 1600),
        "sgp_voc": rng.uniform(9_000, 25_000),
        "sgp_nox": rng.uniform(7_000, 21_000),
    }
    rows = []
    for i in range(91):
        t = i / 90.0
        # A synthetic rise then decay, not a measured odor exposure.
        shape = 0.0 if i < 30 else (0.8 + 0.2 * math.sin((i - 30)/30 * math.pi)) if i < 60 else 0.65 * (90 - i)/30
        scaled = response * shape
        row = {"epoch_ms": end - 90_000 + i * 1_000, "led": "ambient"}
        for field, base in bases.items():
            if field == "bme_t":
                row[field] = round(base + scaled * 2 + rng.gauss(0, 0.04), 5)
            elif field == "bme_rh":
                row[field] = round(base + scaled * 4 + rng.gauss(0, 0.2), 5)
            else:
                row[field] = round(max(base * 0.1, base * (1 + scaled + rng.gauss(0, noise))), 5)
        rows.append(row)
    return rows, f"synthetic_{secrets.token_hex(12)}", end
