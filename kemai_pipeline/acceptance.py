"""Stream promotion registry shared by the builder and the operator CLI."""

import json
from pathlib import Path

_PATH = Path(__file__).with_name("acceptance.json")


def _data():
    return json.loads(_PATH.read_text())


def accepted_streams():
    return sorted(_data()["streams"])


def acceptance(stream):
    return _data()["streams"].get(stream)


def require(stream):
    record = acceptance(stream)
    if not record:
        raise ValueError(
            f"{stream}: STREAM_ACCEPTANCE_REQUIRED; promote only after transaction, "
            "rollback and real-window evidence")
    return record
