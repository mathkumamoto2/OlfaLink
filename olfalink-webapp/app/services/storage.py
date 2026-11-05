"""Storage boundary. Replace backends without changing API or UI."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Protocol
from uuid import uuid4
from app.schemas import Reading, ReadingIn, Source


class ReadingStore(Protocol):
    def recent(self, limit: int = 60) -> list[Reading]: ...
    def insert(self, data: ReadingIn) -> Reading: ...


class DemoStore:
    """Deterministic sample data. Not durable or shared across Cloud Run instances."""
    def __init__(self) -> None:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        samples = [1,1,2,1,2,2,1,2,3,2,3,3,2,3,2,3,4,3,3,2,2,3,3,2]
        self._rows = [
            Reading(id=f"demo-{i:02d}", device_id="olfalink-01",
                    timestamp=now-timedelta(minutes=5*(len(samples)-i-1)),
                    intensity=level, source=Source.DEMO,
                    channels={"bme688_raw":float(5100+180*i),"sgp41_raw":float(12000+135*i)})
            for i,level in enumerate(samples)
        ]
        self._lock = Lock()

    def recent(self, limit: int = 60) -> list[Reading]:
        with self._lock:
            return sorted(self._rows, key=lambda r: r.timestamp, reverse=True)[:limit]

    def insert(self, data: ReadingIn) -> Reading:
        reading = Reading(id=str(uuid4()),device_id=data.device_id,
                          timestamp=data.timestamp or datetime.now(timezone.utc),
                          intensity=data.intensity,source=Source.DEVICE,channels=data.channels)
        with self._lock:
            self._rows.append(reading)
            self._rows = sorted(self._rows, key=lambda r: r.timestamp)[-1000:]
        return reading


class FirestoreStore:
    """Persistent Cloud Run store; uses Application Default Credentials."""
    def __init__(self, collection_name: str) -> None:
        from google.cloud import firestore
        self._collection = firestore.Client().collection(collection_name)

    def recent(self, limit: int = 60) -> list[Reading]:
        from google.cloud.firestore_v1 import Query
        query = self._collection.order_by("timestamp", direction=Query.DESCENDING).limit(limit)
        return [Reading.model_validate({"id":doc.id,**doc.to_dict()}) for doc in query.stream()]

    def insert(self, data: ReadingIn) -> Reading:
        record = Reading(id=str(uuid4()), device_id=data.device_id,
                         timestamp=data.timestamp or datetime.now(timezone.utc),
                         intensity=data.intensity, source=Source.DEVICE, channels=data.channels)
        body=record.model_dump(mode="python",exclude={"id"})
        body["source"]=record.source.value
        self._collection.document(record.id).create(body)
        return record
