"""Private, atomic capture manifests. Durable data before network writes."""
import json
import os
from pathlib import Path
import tempfile
import uuid
from .contracts import digest
from .capabilities import capture_method


class ArtifactError(ValueError):
    pass


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name)
    try:
        with os.fdopen(fd,'wb') as handle:
            handle.write(data);handle.flush();os.fsync(handle.fileno())
        os.replace(tmp,path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


class CaptureStore:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, capture_id):
        if not isinstance(capture_id,str) or len(capture_id)!=32 or any(c not in '0123456789abcdef' for c in capture_id):
            raise ArtifactError('Invalid capture ID')
        return self.root / capture_id

    def create(self, *, tenant, stream, window_key, source_key_version, mode, method=None):
        capture_id=uuid.uuid4().hex
        method=method or capture_method(stream,provisional=mode=='provisional')
        state={'schema_version':1,'capture_id':capture_id,'tenant':tenant,'stream':stream,
            'window_key':window_key,'source_key_version':source_key_version,'mode':mode,
            'method':method,'phase':'capturing','pages':[],'batches':[],'receipts':{},'evidence':None}
        self.save(state)
        return state

    def save(self, state):
        atomic_json(self.path(state['capture_id'])/'manifest.json',state)

    def load(self, capture_id):
        state=json.loads((self.path(capture_id)/'manifest.json').read_text())
        if state.get('capture_id')!=capture_id: raise ArtifactError('Capture identity mismatch')
        return state

    def append_page(self, state, rows):
        if state['phase']!='capturing': raise ArtifactError('Capture already sealed')
        index=len(state['pages']);value={'rows':rows}
        name=f'page-{index:06}.json'
        atomic_json(self.path(state['capture_id'])/name,value)
        state['pages'].append({'file':name,'rows':len(rows),'hash':digest(value)})
        self.save(state)

    def rows(self, state):
        for item in state['pages']:
            if Path(item['file']).name!=item['file']: raise ArtifactError('Invalid artifact path')
            value=json.loads((self.path(state['capture_id'])/item['file']).read_text())
            if digest(value)!=item['hash'] or len(value['rows'])!=item['rows']:
                raise ArtifactError('Artifact digest or row count mismatch')
            yield from value['rows']

    def seal(self, state, evidence):
        if state['phase']!='capturing': raise ArtifactError('Capture cannot be sealed twice')
        keys=[row['source_key'] for row in self.rows(state)]
        if len(keys)!=len(set(keys)): raise ArtifactError('Duplicate source keys across pages')
        if evidence.get('source_rows')!=len(keys): raise ArtifactError('Source count mismatch')
        if not evidence.get('verification_method'): raise ArtifactError('Missing verification method')
        state.update(phase='captured',evidence=evidence)
        self.save(state)

    def interrupt_incomplete(self, capture_id):
        state=self.load(capture_id)
        if state['phase']=='capturing':
            state.update(phase='interrupted',reason='unstable_offset_requires_new_capture')
            self.save(state)
        return state
