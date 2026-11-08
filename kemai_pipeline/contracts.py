"""Pure batch identity and evidence contracts, shared across adapters."""

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Batch:
    tenant: str
    stream: str
    window_key: str
    capture_id: str
    sequence: int
    source_key_version: str
    records: tuple[dict, ...]

    def envelope(self):
        if not self.capture_id or type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("capture_id and non-negative batch sequence required")
        keys = [record.get("source_key") for record in self.records]
        if not keys or any(not isinstance(key, str) or not key for key in keys):
            raise ValueError("Nonempty records with string source_key required")
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate source_key within batch")
        # Content never participates in identity: changed content must conflict.
        return {"schema_version": 1, "tenant": self.tenant, "stream": self.stream,
                "window_key": self.window_key, "capture_id": self.capture_id,
                "batch_id": digest([self.tenant, self.stream, self.capture_id, self.sequence]),
                "sequence": self.sequence, "source_key_version": self.source_key_version,
                "records": list(self.records), "payload_hash": digest(self.records)}


class DataCenter(Protocol):
    def batch_upsert(self, batch: dict) -> dict: ...
    def batch_result(self, batch_id: str) -> dict: ...
    def commit_window(self, manifest: dict) -> dict: ...
