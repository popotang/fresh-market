"""Private durable detail snapshots and independently verified line receipts."""
import json
from datetime import datetime, timezone
from .artifacts import atomic_json, ArtifactError
from .contracts import digest


class LineEvidence:
    def __init__(self, root, tenant):
        self.root = root / 'detail-evidence' / digest(str(tenant))

    def directory(self, kind, doc):
        return self.root / kind / digest([str(doc['id']), str(doc['source_document_id'])])

    def load(self, kind, doc):
        directory = self.directory(kind, doc)
        path = directory / 'latest.json'
        if not path.exists():
            return None
        index = json.loads(path.read_text())
        sha = index.get('sha256', '')
        if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ArtifactError('Invalid detail snapshot digest')
        snapshot = json.loads((directory / (sha + '.json')).read_text())
        if digest(snapshot) != sha:
            raise ArtifactError('Detail snapshot digest mismatch')
        if (snapshot['kind'] != kind or str(snapshot['document_id']) != str(doc['id'])
                or str(snapshot['source_id']) != str(doc['source_document_id'])):
            raise ArtifactError('Detail snapshot scope mismatch')
        # An unknown source version cannot prove that an old snapshot is current.
        if doc.get('source_version') is None or snapshot['version'] != str(doc['source_version']):
            return None
        return sha, snapshot

    def save(self, kind, doc, entity, capture_id):
        snapshot = {'schema_version': 1, 'kind': kind, 'document_id': str(doc['id']),
                    'source_id': str(doc['source_document_id']), 'version':
                    None if entity.get('version') is None else str(entity['version']),
                    'capture_id': capture_id, 'observed_at': datetime.now(timezone.utc).isoformat(),
                    'entity': entity}
        # Stored-payload adapters may use Decimal. Preserve their exact decimal text.
        snapshot = json.loads(json.dumps(snapshot, default=str, allow_nan=False))
        sha = digest(snapshot)
        directory = self.directory(kind, doc)
        atomic_json(directory / (sha + '.json'), snapshot)
        atomic_json(directory / 'latest.json', {'sha256': sha})
        return sha, snapshot

    def receipt(self, kind, doc):
        path = self.directory(kind, doc) / 'lines-verified.json'
        return json.loads(path.read_text()) if path.exists() else None

    def confirm(self, kind, doc, sha, row_count, capture_id):
        atomic_json(self.directory(kind, doc) / 'lines-verified.json', {
            'schema_version': 1, 'scope': 'lines_only', 'snapshot_sha256': sha,
            'row_count': row_count, 'capture_id': capture_id,
            'verified_at': datetime.now(timezone.utc).isoformat()})
