"""Load and self-verify export-mapping verification artifacts.

Artifact schema (written by scripts/verify_kemai_history_export_mapping.py,
validated here):
{schema_version:1, stream, mapping_version, header_sha256, sample_rows,
 key_coverage, verifier, verified_at, artifact_sha256}

Tamper-evidence: artifact_sha256 is the sha256 hex digest of the canonical
serialization of every field except artifact_sha256 itself, where canonical
serialization is kemai_pipeline.normalize.dump (JSON, sort_keys, compact
separators, ensure_ascii=False, allow_nan=False). Any edit to any covered
field without recomputing the digest fails verification; the digest field
itself must match the recomputation.
"""
import hashlib
import json
from pathlib import Path

from .export_mappings import HISTORY_EXPORT_FIELDS, MAPPING_VERSION
from .normalize import dump

SCHEMA_VERSION=1
REQUIRED_FIELDS=('schema_version','stream','mapping_version','header_sha256','sample_rows',
                 'key_coverage','verifier','verified_at','artifact_sha256')


def expected_header_digest(mapping=HISTORY_EXPORT_FIELDS):
    """sha256 hex over the canonical dump of the mapping's header names in
    order — the same algorithm scripts/verify_kemai_history_export_mapping.py
    uses when it writes header_sha256 into the artifact. An artifact whose
    header_sha256 differs from the current mapping's digest is untrusted even
    if its self-check passes."""
    return hashlib.sha256(dump(list(mapping.keys())).encode()).hexdigest()


def canonical_artifact_digest(artifact):
    """sha256 hex over the canonical dump of the artifact minus artifact_sha256."""
    covered={key:value for key,value in artifact.items() if key!='artifact_sha256'}
    return hashlib.sha256(dump(covered).encode()).hexdigest()


def _is_hex64(value):
    return (isinstance(value,str) and len(value)==64 and
            all(char in '0123456789abcdef' for char in value))


def _is_positive_int(value):
    return isinstance(value,int) and not isinstance(value,bool) and value>0


def _valid(artifact,stream):
    if not isinstance(artifact,dict) or any(field not in artifact for field in REQUIRED_FIELDS):
        return False
    if artifact['schema_version']!=SCHEMA_VERSION or artifact['stream']!=stream:
        return False
    if artifact['mapping_version']!=MAPPING_VERSION:  # stale mapping -> untrusted
        return False
    if not _is_hex64(artifact['header_sha256']):
        return False
    if artifact['header_sha256']!=expected_header_digest():  # stale/forged header -> untrusted
        return False
    coverage=artifact['key_coverage']
    if isinstance(coverage,bool) or not isinstance(coverage,(int,float)) or coverage!=1.0:
        return False
    if not _is_positive_int(artifact['sample_rows']):
        return False
    if not isinstance(artifact['verifier'],str) or not artifact['verifier']:
        return False
    if not isinstance(artifact['verified_at'],str) or not artifact['verified_at']:
        return False
    if not _is_hex64(artifact['artifact_sha256']):
        return False
    try:
        return canonical_artifact_digest(artifact)==artifact['artifact_sha256']
    except (TypeError,ValueError):
        return False


def load_verification(stream,root):
    """读 <root>/export-mapping/<stream>.json，验证通过返回 dict，否则 None。不抛异常（缺文件/坏 JSON/校验失败都返回 None）。"""
    path=Path(root)/'export-mapping'/f'{stream}.json'
    try:
        artifact=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        return None
    try:
        return artifact if _valid(artifact,stream) else None
    except Exception:
        return None
