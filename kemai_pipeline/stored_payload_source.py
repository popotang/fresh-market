"""Read purchase detail entities only from already-captured parent payloads.

This source never invokes OpenCLI and never touches the Kemai SaaS. Identity,
version and payload hash must match the durable parent row; malformed or stale
payloads fail closed without echoing business content.
"""
import hashlib
import json

from .source import SourceError


class StoredPayloadVersionMismatch(SourceError):
    def __init__(self, entity_version, payload_hash):
        super().__init__('Stored purchase payload version mismatch')
        self.entity_version = entity_version
        self.payload_hash = payload_hash


class StoredPayloadSource:
    def __init__(self, center):
        self.center = center
        self.operations = 0

    @staticmethod
    def _hash(entity):
        payload = json.dumps(entity, ensure_ascii=False, separators=(",", ":"),
                             sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def document_detail(self, kind, source_id):
        rows = self.center.read_all(
            'purchase_documents',
            where={'document_kind': {'$eq': kind},
                   'source_document_id': {'$eq': str(source_id)}},
            select=['id', 'source_document_id', 'source_version', 'payload', 'payload_hash'])
        if len(rows) != 1:
            raise SourceError('Stored purchase parent identity is not unique')
        parent = rows[0]
        payload = parent.get('payload')
        try:
            entity = json.loads(payload) if isinstance(payload, str) else payload
        except (TypeError, ValueError):
            raise SourceError('Stored purchase payload is malformed') from None
        if not isinstance(entity, dict) or not entity:
            raise SourceError('Stored purchase payload is not an object')
        if str(entity.get('id')) != str(source_id):
            raise SourceError('Stored purchase payload identity mismatch')
        entity_hash = self._hash(entity)
        if (not parent.get('payload_hash') or entity_hash != parent['payload_hash']):
            raise SourceError('Stored purchase payload hash mismatch')
        if (parent.get('source_version') is not None
                and str(entity.get('version')) != str(parent['source_version'])):
            raise StoredPayloadVersionMismatch(entity.get('version'), entity_hash)
        return entity
