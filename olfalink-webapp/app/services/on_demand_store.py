"""Persistent, idempotent sensor ingest and bounded reads for on-demand inference.

The AWS Lambda bridge pushes measurements, but inference never runs on ingest.
A sample is uniquely keyed by (device_id, session_id, phase, epoch_ms).
Firestore's document-per-sample layout survives Cloud Run scale-to-zero.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol
from app.services.live_stream import LiveBatch, SensorSample

FRESH_MS = 120_000
MAX_PAST_MS = 300_000
MAX_FUTURE_MS = 60_000
PHASES = {"baseline", "exposure", "refresh", "ambient"}

class MeasurementUnavailable(Exception):
    pass


def validate(batch: LiveBatch, *, now_ms: int | None = None) -> None:
    now = int(time.time() * 1000) if now_ms is None else now_ms
    stamps = [sample.epoch_ms for sample in batch.samples]
    if stamps != sorted(set(stamps)):
        raise ValueError("timestamps must be strictly increasing and unique")
    if stamps[0] < now - MAX_PAST_MS or stamps[-1] > now + MAX_FUTURE_MS:
        raise ValueError("sensor timestamps must be within the last five minutes")


class LiveStore(Protocol):
    def ingest(self, batch: LiveBatch) -> int: ...
    def status(self, device_id: str) -> dict: ...
    def window(self, device_id: str) -> tuple[list[dict], str, int]: ...
    def latest_result(self, device_id: str) -> dict | None: ...
    def save_result(self, device_id: str, session_id: str, end_ms: int, dashboard: dict) -> None: ...


class FirestoreOnDemandStore:
    def __init__(self, *, client=None, collection: str = "olfalink_live_v11"):
        if client is None:
            from google.cloud import firestore
            client = firestore.Client()
        self.db = client
        self.root = client.collection(collection)

    def _device(self, device_id: str):
        return self.root.document(device_id)

    def ingest(self, batch: LiveBatch) -> int:
        validate(batch)
        dev = self._device(batch.device_id)
        sess = dev.collection("sessions").document(batch.session_id)
        from google.cloud import firestore
        txn = self.db.transaction()
        @firestore.transactional
        def commit(transaction):
            current = dev.get(transaction=transaction)
            previous = current.to_dict() if current.exists else {}
            for sample in batch.samples:
                row = sample.model_dump()
                ref = sess.collection(sample.led).document(f"{sample.epoch_ms:013d}")
                transaction.set(ref, row)
            if batch.samples[-1].epoch_ms >= int(previous.get("last_ms", 0)):
                transaction.set(dev, {"session_id":batch.session_id,
                                      "last_ms":batch.samples[-1].epoch_ms,
                                      "last_led":batch.samples[-1].led})
        commit(txn)
        return len(batch.samples)

    def status(self, device_id: str) -> dict:
        snap = self._device(device_id).get()
        if not snap.exists:
            return {"connected": False, "last_ms": None, "phase": None, "session_id": None}
        state = snap.to_dict()
        ms = int(state.get("last_ms", 0))
        fresh = 0 <= int(time.time()*1000) - ms <= FRESH_MS
        return {"connected":fresh, "last_ms":ms or None,
                "phase":state.get("last_led"), "session_id":state.get("session_id")}

    def window(self, device_id: str) -> tuple[list[dict], str, int]:
        state = self.status(device_id)
        if not state["connected"]:
            raise MeasurementUnavailable("センサーから新しいデータが届いていません。")
        if state["phase"] == "refresh":
            raise MeasurementUnavailable("回復区間の測定中です。")
        if state["phase"] == "baseline":
            raise MeasurementUnavailable("基準空気を測定しています。")
        session = state["session_id"]
        doc = self._device(device_id).collection("sessions").document(session)
        from google.cloud.firestore_v1 import Query
        def read_phase(phase: str, limit: int, reverse: bool = True) -> list[dict]:
            q = doc.collection(phase).order_by("epoch_ms", direction=Query.DESCENDING if reverse else Query.ASCENDING).limit(limit)
            return [record.to_dict() for record in q.stream()]
        if state["phase"] == "ambient":
            rows = read_phase("ambient", 120)
            rows.reverse()
            return rows, session, int(state["last_ms"])
        baseline = read_phase("baseline", 90)
        exposure = read_phase("exposure", 120)
        first = read_phase("exposure", 1, reverse=False)
        by_time = {int(row["epoch_ms"]): row for row in first + exposure}
        exposure = sorted(by_time.values(), key=lambda r:r["epoch_ms"])
        baseline.sort(key=lambda r:r["epoch_ms"])
        return baseline + exposure, session, int(state["last_ms"])

    def latest_result(self, device_id: str) -> dict | None:
        ref = self._device(device_id).collection("analysis").document("latest")
        snap = ref.get()
        return snap.to_dict() if snap.exists else None

    def save_result(self, device_id: str, session_id: str, end_ms: int, dashboard: dict) -> None:
        self._device(device_id).collection("analysis").document("latest").set({
            "session_id":session_id, "end_ms":end_ms,
            "computed_ms":int(time.time()*1000), "dashboard":dashboard})


class MemoryOnDemandStore:
    """Testing/local-only implementation. Never choose this on Cloud Run."""
    def __init__(self):
        self._lock = RLock()
        self._sessions: dict[str, dict[str, list[dict]]] = {}
        self._state: dict[str, dict] = {}
        self._cached: dict[str, dict] = {}

    def ingest(self, batch: LiveBatch) -> int:
        validate(batch)
        with self._lock:
            key = batch.device_id + "/" + batch.session_id
            saved = self._sessions.setdefault(key, {})
            for s in batch.samples:
                rows = saved.setdefault(s.led, [])
                rows[:] = [x for x in rows if x["epoch_ms"] != s.epoch_ms]
                rows.append(s.model_dump())
                rows.sort(key=lambda x:x["epoch_ms"])
            ms = batch.samples[-1].epoch_ms
            if ms >= self._state.get(batch.device_id, {}).get("last_ms", 0):
                self._state[batch.device_id] = {
                    "session_id":batch.session_id, "last_ms":ms, "last_led":batch.samples[-1].led}
        return len(batch.samples)

    def status(self, device_id: str) -> dict:
        with self._lock:
            state = self._state.get(device_id, {}).copy()
        ms = int(state.get("last_ms", 0))
        fresh = 0 <= int(time.time()*1000) - ms <= FRESH_MS
        return {"connected":fresh, "last_ms":ms or None,
                "phase":state.get("last_led"), "session_id":state.get("session_id")}

    def window(self, device_id: str) -> tuple[list[dict], str, int]:
        state = self.status(device_id)
        if not state["connected"]:
            raise MeasurementUnavailable("センサーから新しいデータが届いていません。")
        if state["phase"] == "refresh":
            raise MeasurementUnavailable("回復区間の測定中です。")
        if state["phase"] == "baseline":
            raise MeasurementUnavailable("基準空気を測定しています。")
        session = state["session_id"]
        with self._lock:
            by_phase = self._sessions.get(device_id + "/" + session, {})
            if state["phase"] == "ambient":
                return list(by_phase.get("ambient", [])[-120:]), session, state["last_ms"]
            baseline = list(by_phase.get("baseline", [])[-90:])
            exposure = by_phase.get("exposure", [])
            exposure = list(dict((r["epoch_ms"],r) for r in exposure[:1] + exposure[-120:]).values())
        return baseline + exposure, session, state["last_ms"]

    def latest_result(self, device_id: str) -> dict | None:
        with self._lock:
            record = self._cached.get(device_id)
            return record.copy() if record else None

    def save_result(self, device_id: str, session_id: str, end_ms: int, dashboard: dict) -> None:
        with self._lock:
            self._cached[device_id] = {"session_id":session_id, "end_ms":end_ms,
               "computed_ms":int(time.time()*1000), "dashboard":dashboard}
