"""Shared filesystem-backed Kemai rate-limit circuit breaker."""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path


from .project_paths import RUNTIME_LOGS, RUNTIME_STATE

STATE=Path(os.environ.get("KEMAI_RATE_LIMIT_STATE") or RUNTIME_STATE/"kemai_rate_limit_status.json")
LOG=Path(os.environ.get("KEMAI_RATE_LIMIT_LOG") or RUNTIME_LOGS/"kemai_rate_limit.log")


def is_rate_limited(message):
    text=str(message)
    return bool(re.search(
        r"(?:\b429\b|too\s+many\s+requests?|rate[\s_-]*limit(?:ed)?|"
        r"(?:您的)?操作(?:过于)?频繁|请求(?:过于)?频繁|太频繁|请?稍后再试)",
        text,
        re.IGNORECASE,
    ))


def rate_limit_signal(value):
    """Return the smallest useful limit signal from a decoded API/CLI value."""
    if isinstance(value,dict):
        selected={k:value.get(k) for k in ("httpStatus","status","code","message","msg","error") if value.get(k) is not None}
        if value.get("rateLimited") is True:
            return str(selected)
        if value.get("httpStatus") == 429 or value.get("status") == 429 or value.get("code") == 429:
            return str(selected)
        for key in ("message","msg","error","failReason"):
            if value.get(key) is not None and is_rate_limited(value[key]):
                return str(selected or {key:value[key]})
        for key in ("data","response","result"):
            signal=rate_limit_signal(value.get(key))
            if signal:
                return signal
    elif isinstance(value,list):
        for item in value:
            signal=rate_limit_signal(item)
            if signal:
                return signal
    elif is_rate_limited(value):
        return str(value)
    return None


def _write(state):
    STATE.parent.mkdir(parents=True,exist_ok=True)
    temp=STATE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n")
    os.chmod(temp,0o600)
    temp.replace(STATE)


def honor_cooldown():
    if not STATE.exists():
        return
    try:
        state=json.loads(STATE.read_text())
        resume_after=float(state.get("resume_after_epoch") or 0)
    except (OSError,ValueError,TypeError,json.JSONDecodeError):
        return
    announced=False
    while time.time()<resume_after:
        if not announced:
            print(f"global rate-limit cooldown until {state.get('resume_after')}",flush=True)
            announced=True
        time.sleep(min(30,max(1,resume_after-time.time())))


def record_limit(command,message,wait=True):
    now=time.time(); previous={}
    if STATE.exists():
        try:
            previous=json.loads(STATE.read_text())
        except (OSError,ValueError,json.JSONDecodeError):
            previous={}
    last=float(previous.get("last_event_epoch") or 0)
    consecutive=(int(previous.get("consecutive") or 0)+1) if now-last<1800 else 1
    # Kemai currently needs a full 30-minute quiet window after every hit.
    cooldown=1800
    resume_epoch=now+cooldown
    event={
        "state":"cooling","detected_at":datetime.now().astimezone().isoformat(timespec="seconds"),
        "last_event_epoch":now,"resume_after_epoch":resume_epoch,
        "resume_after":datetime.fromtimestamp(resume_epoch).astimezone().isoformat(timespec="seconds"),
        "cooldown_seconds":cooldown,"consecutive":consecutive,"command":command,
        "message":str(message)[-500:],
    }
    _write(event)
    LOG.parent.mkdir(parents=True,exist_ok=True)
    with LOG.open("a") as log:
        log.write(json.dumps(event,ensure_ascii=False)+"\n")
    os.chmod(LOG,0o600)
    print(f"RATE_LIMIT detected; cooling {cooldown}s; log={LOG}",flush=True)
    if wait:
        honor_cooldown()


def extend_cooldown(seconds,reason="manual"):
    now=time.time(); resume_epoch=now+int(seconds)
    previous={}
    if STATE.exists():
        try:
            previous=json.loads(STATE.read_text())
        except (OSError,ValueError,json.JSONDecodeError):
            previous={}
    event={**previous,
        "state":"cooling","extended_at":datetime.now().astimezone().isoformat(timespec="seconds"),
        "resume_after_epoch":resume_epoch,
        "resume_after":datetime.fromtimestamp(resume_epoch).astimezone().isoformat(timespec="seconds"),
        "cooldown_seconds":int(seconds),"extension_reason":reason,
    }
    _write(event)
    LOG.parent.mkdir(parents=True,exist_ok=True)
    with LOG.open("a") as log:
        log.write(json.dumps({"event":"cooldown_extended",**event},ensure_ascii=False)+"\n")
    os.chmod(LOG,0o600)
    return event


def mark_ready():
    if not STATE.exists():
        return
    try:
        state=json.loads(STATE.read_text())
    except (OSError,ValueError,json.JSONDecodeError):
        return
    if state.get("state")=="cooling" and time.time()>=float(state.get("resume_after_epoch") or 0):
        state["state"]="ready"
        state["last_success_at"]=datetime.now().astimezone().isoformat(timespec="seconds")
        _write(state)
