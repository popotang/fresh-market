#!/usr/bin/env python3
"""Offline drift check and explicit, backup-first install for the Kemai OpenCLI adapter."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "kemai_opencli"
DEFAULT_RUNTIME = Path.home() / ".opencli" / "clis" / "kemai"
DEFAULT_GATE = Path.home() / ".opencli" / "kemai-source-gate"
INSTALLABLE_SUFFIXES = {".js", ".json"}


class RuntimeInstallError(RuntimeError):
    pass


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _lexists(path):
    return os.path.lexists(path)


def _read_regular(path, label):
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise ValueError(f"cannot read {label} SHA-256: {error.strerror or error}") from error
    if not stat.S_ISREG(mode):
        raise ValueError(f"cannot read {label} SHA-256: expected a non-symlink regular file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ValueError(f"cannot read {label} SHA-256: {error.strerror or error}") from error


def _load_manifest(source_data, errors):
    raw = source_data.get("manifest.json")
    if raw is None:
        errors.append({"path": "manifest.json", "error": "source manifest.json is missing"})
        return {}
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        errors.append({"path": "manifest.json", "error": f"invalid source manifest: {error}"})
        return {}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("commands"), list):
        errors.append({"path": "manifest.json", "error": "invalid source manifest: commands must be an array"})
        return {}
    return manifest


def _file_snapshot(source, runtime):
    errors = []
    source_data = {}
    if source.is_symlink() or not source.is_dir():
        errors.append({"path": ".", "error": "source directory is missing, not a directory, or is a symlink"})
    else:
        try:
            entries = sorted(source.iterdir(), key=lambda path: path.name)
        except OSError as error:
            entries = []
            errors.append({"path": ".", "error": f"cannot list source directory: {error}"})
        for entry in entries:
            if entry.suffix not in INSTALLABLE_SUFFIXES:
                continue
            try:
                source_data[entry.name] = _read_regular(entry, f"source {entry.name}")
            except ValueError as error:
                errors.append({"path": entry.name, "error": str(error)})

    expected = set(source_data)
    missing = []
    extra = []
    changed = []
    matched = 0
    runtime_data = {}

    if _lexists(runtime) and (runtime.is_symlink() or not runtime.is_dir()):
        errors.append({"path": ".", "error": "runtime path is not a non-symlink directory"})
    elif runtime.is_dir():
        try:
            entries = sorted(runtime.iterdir(), key=lambda path: path.name)
        except OSError as error:
            entries = []
            errors.append({"path": ".", "error": f"cannot list runtime directory: {error}"})
        for entry in entries:
            if entry.name not in expected:
                extra.append(entry.name)

    for name in sorted(expected):
        target = runtime / name
        if not _lexists(target):
            missing.append(name)
            runtime_data[name] = None
            continue
        try:
            actual = _read_regular(target, f"runtime {name}")
            runtime_data[name] = actual
        except ValueError as error:
            errors.append({"path": name, "error": str(error)})
            continue
        expected_digest = _sha256(source_data[name])
        actual_digest = _sha256(actual)
        if expected_digest == actual_digest:
            matched += 1
        else:
            changed.append({
                "path": name,
                "expected_sha256": expected_digest,
                "actual_sha256": actual_digest,
            })

    manifest = _load_manifest(source_data, errors)
    report = {
        "expected_count": len(expected),
        "matched_count": matched,
        "missing": missing,
        "extra": extra,
        "changed": changed,
        "errors": errors,
        "expected_sha256": {name: _sha256(data) for name, data in sorted(source_data.items())},
    }
    return report, manifest, source_data, runtime_data


def _probe_version(executable):
    path = shutil.which(executable)
    if path is None:
        return {"path": None, "available": False, "version": None, "error": "executable not found"}
    try:
        completed = subprocess.run(
            [path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"path": path, "available": False, "version": None, "error": str(error)}
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0:
        return {
            "path": path,
            "available": False,
            "version": output or None,
            "error": f"--version exited {completed.returncode}",
        }
    return {"path": path, "available": True, "version": output, "error": None}


def _version_status(probe, compatible):
    return {**probe, "compatible": bool(compatible)}


def _read_gate_json(path, errors):
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        errors.append({"path": path.name, "error": str(error)})
        return {}
    if not isinstance(value, dict):
        errors.append({"path": path.name, "error": "gate state must be a JSON object"})
        return {}
    return value


def _gate_snapshot(gate):
    errors = []
    config = _read_gate_json(gate / "config.json", errors)
    source_state = _read_gate_json(gate / "source-state.json", errors)
    candidates = [_read_gate_json(gate / "rate-limit-status.json", errors)]
    legacy = config.get("legacy_rate_status")
    if isinstance(legacy, str) and legacy:
        candidates.append(_read_gate_json(Path(legacy).expanduser(), errors))
    resume_values = []
    for value in candidates:
        try:
            resume_values.append(float(value.get("resume_after_epoch") or 0))
        except (TypeError, ValueError):
            errors.append({"path": "rate-limit-status.json", "error": "invalid resume_after_epoch"})
    resume_after = max(resume_values, default=0.0)
    remaining = max(0.0, resume_after - time.time())
    return {
        "directory": str(gate),
        "config_present": (gate / "config.json").is_file(),
        "configured": bool(config.get("script_path")),
        "source_state": source_state,
        "cooldown": {
            "state": "cooling" if remaining > 0 else "ready",
            "resume_after_epoch": resume_after,
            "remaining_seconds": int(math.ceil(remaining)),
        },
        "errors": errors,
    }


def check_runtime(source_dir=DEFAULT_SOURCE, runtime_dir=DEFAULT_RUNTIME, gate_dir=DEFAULT_GATE,
                  node_bin="node", opencli_bin="opencli"):
    source = Path(source_dir).expanduser()
    runtime = Path(runtime_dir).expanduser()
    gate = Path(gate_dir).expanduser()
    files, manifest, _, _ = _file_snapshot(source, runtime)
    requirements = manifest.get("runtime_requirements", {})
    node = _probe_version(str(node_bin))
    opencli = _probe_version(str(opencli_bin))
    node_match = re.search(r"\bv?(\d+)(?:\.\d+){0,2}", node.get("version") or "")
    minimum_node = int(requirements.get("node_minimum_major", 20))
    node_compatible = bool(node["available"] and node_match and int(node_match.group(1)) >= minimum_node)
    expected_opencli = str(requirements.get("opencli_version", ""))
    opencli_compatible = bool(
        opencli["available"] and expected_opencli
        and any(re.search(rf"(?<![\d.]){re.escape(version)}(?![\d.])", opencli.get("version") or "")
                for version in requirements.get("opencli_supported_versions", [expected_opencli]))
    )
    files_ok = not any(files[key] for key in ("missing", "extra", "changed", "errors"))
    return {
        "schema_version": 1,
        "action": "check",
        "ok": files_ok and node_compatible and opencli_compatible,
        "source_dir": str(source),
        "runtime_dir": str(runtime),
        "adapter": manifest.get("adapter"),
        "adapter_version": manifest.get("adapter_version"),
        "manifest_schema_version": manifest.get("schema_version"),
        "files": files,
        "node": _version_status(node, node_compatible),
        "opencli": _version_status(opencli, opencli_compatible),
        "gate": _gate_snapshot(gate),
        "authentication": None,
        "source_acceptance": "not_checked",
        "source_requests": 0,
        "db_writes": 0,
    }


def _atomic_write(path, data, mode=0o600):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        temporary.replace(path)
    finally:
        if _lexists(temporary):
            temporary.unlink()


def _create_backup(backup_root, source, runtime, runtime_data):
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="adapter-backup-", dir=backup_root))
    evidence = []
    for name, data in sorted(runtime_data.items()):
        if data is None:
            continue
        _atomic_write(backup / name, data)
        evidence.append({"path": name, "sha256": _sha256(data)})
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source),
        "runtime_dir": str(runtime),
        "files": evidence,
    }
    _atomic_write(backup / "backup-manifest.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
    return backup, evidence


def _restore(runtime, runtime_data):
    for name, data in runtime_data.items():
        target = runtime / name
        if data is None:
            if _lexists(target) and (target.is_file() or target.is_symlink()):
                target.unlink()
        else:
            _atomic_write(target, data)


def install_runtime(source_dir=DEFAULT_SOURCE, runtime_dir=DEFAULT_RUNTIME, gate_dir=DEFAULT_GATE,
                    backup_root=None, node_bin="node", opencli_bin="opencli"):
    source = Path(source_dir).expanduser()
    runtime = Path(runtime_dir).expanduser()
    gate = Path(gate_dir).expanduser()
    if source.resolve() == runtime.resolve():
        raise RuntimeInstallError("refusing install: source and runtime are the same path")
    backup_root = Path(backup_root).expanduser() if backup_root else gate / "adapter-backups"

    files, manifest, source_data, runtime_data = _file_snapshot(source, runtime)
    if files["extra"]:
        raise RuntimeInstallError("refusing install: unknown runtime extra files: " + ", ".join(files["extra"]))
    if files["errors"]:
        raise RuntimeInstallError("refusing install: SHA-256 digest or manifest anomaly: " + json.dumps(files["errors"], ensure_ascii=False))
    if not source_data or not manifest:
        raise RuntimeInstallError("refusing install: source manifest or installable files are missing")

    gate_config = gate / "config.json"
    gate_before = _sha256(_read_regular(gate_config, "gate config")) if _lexists(gate_config) else None
    backup = None
    evidence = []
    changed_names = [
        name for name, data in sorted(source_data.items())
        if runtime_data.get(name) is None or _sha256(runtime_data[name]) != _sha256(data)
    ]
    try:
        if runtime.is_dir() and any(data is not None for data in runtime_data.values()):
            backup, evidence = _create_backup(backup_root, source, runtime, runtime_data)
        runtime.mkdir(parents=True, exist_ok=True)
        for name, data in sorted(source_data.items()):
            current_source = _read_regular(source / name, f"source {name}")
            if _sha256(current_source) != _sha256(data):
                raise RuntimeInstallError(f"source {name} SHA-256 changed during install")
            target = runtime / name
            original = runtime_data.get(name)
            if original is None and _lexists(target):
                raise RuntimeInstallError(f"runtime {name} appeared during install")
            if original is not None:
                current_runtime = _read_regular(target, f"runtime {name}")
                if _sha256(current_runtime) != _sha256(original):
                    raise RuntimeInstallError(f"runtime {name} SHA-256 changed during install")
            mode = stat.S_IMODE((source / name).stat().st_mode) & 0o777
            _atomic_write(target, data, mode=mode)
            if _sha256(_read_regular(target, f"installed {name}")) != _sha256(data):
                raise RuntimeInstallError(f"installed {name} SHA-256 verification failed")
    except Exception as error:
        try:
            if runtime.is_dir():
                _restore(runtime, runtime_data)
        except Exception as restore_error:
            raise RuntimeInstallError(f"install failed and restore failed: {error}; {restore_error}") from error
        if isinstance(error, RuntimeInstallError):
            raise RuntimeInstallError(f"refusing install: {error}; previous runtime restored") from error
        raise RuntimeInstallError(f"install failed; previous runtime restored: {error}") from error

    report = check_runtime(source, runtime, gate, node_bin=node_bin, opencli_bin=opencli_bin)
    if any(report["files"][key] for key in ("missing", "extra", "changed", "errors")):
        try:
            _restore(runtime, runtime_data)
        except Exception as restore_error:
            raise RuntimeInstallError(f"post-install drift check and restore failed: {restore_error}")
        raise RuntimeInstallError("post-install SHA-256 drift check failed; previous runtime restored")
    gate_after = _sha256(_read_regular(gate_config, "gate config")) if _lexists(gate_config) else None
    report.update({
        "action": "install",
        "installed": changed_names,
        "backup": {
            "path": str(backup) if backup else None,
            "files": evidence,
            "restorable": backup is not None,
        },
        "gate_config_preserved": gate_before == gate_after,
        "gate_config_sha256": gate_after,
    })
    return report


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("check", "install"):
        sub = subparsers.add_parser(action)
        sub.add_argument("--source-dir", default=str(DEFAULT_SOURCE))
        sub.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME))
        sub.add_argument("--gate-dir", default=os.environ.get("KEMAI_SOURCE_GATE_DIR", str(DEFAULT_GATE)))
        sub.add_argument("--node-bin", default="node")
        sub.add_argument("--opencli-bin", default="opencli")
        if action == "install":
            sub.add_argument("--backup-root")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    common = {
        "source_dir": args.source_dir,
        "runtime_dir": args.runtime_dir,
        "gate_dir": args.gate_dir,
        "node_bin": args.node_bin,
        "opencli_bin": args.opencli_bin,
    }
    try:
        if args.action == "install":
            report = install_runtime(backup_root=args.backup_root, **common)
        else:
            report = check_runtime(**common)
    except RuntimeInstallError as error:
        report = {"schema_version": 1, "action": args.action, "ok": False, "error": str(error), "source_requests": 0, "db_writes": 0}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
