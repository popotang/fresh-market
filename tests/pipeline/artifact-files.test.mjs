import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm,stat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {recoverArtifact,publishArtifact} from '../../kemai_opencli/artifact-files.js';
async function fixture(fn){const dir=await mkdtemp(join(tmpdir(),'kemai-artifact-'));try{await fn(dir);}finally{await rm(dir,{recursive:true,force:true});}}
const bytes=Buffer.from('PK-test');const meta={task_id:'123',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
test('publish immutable artifact and recover exact task',()=>fixture(async dir=>{
 const file=join(dir,'export.xlsx'),part=file+'.part';await writeFile(part,bytes,{mode:0o600});
 await publishArtifact(part,file,meta);assert.deepEqual(await recoverArtifact(file,'123'),meta);
 await assert.rejects(recoverArtifact(file,'other'),/ARTIFACT_CONFLICT/);
 assert.equal((await stat(file)).mode&0o777,0o600);
}));
test('recover crash after publishing data but before publishing manifest',()=>fixture(async dir=>{
 const file=join(dir,'export.xlsx');await writeFile(file,bytes);await writeFile(file+'.meta.json.pending',JSON.stringify(meta));
 assert.deepEqual(await recoverArtifact(file,'123'),meta);assert.deepEqual(JSON.parse(await readFile(file+'.meta.json')),meta);
}));
test('reject unauthenticated orphan and mutated bytes',()=>fixture(async dir=>{
 const file=join(dir,'export.xlsx');await writeFile(file,bytes);await assert.rejects(recoverArtifact(file,'123'),/no authenticated/);
 await writeFile(file+'.meta.json',JSON.stringify({...meta,sha256:'wrong'}));await assert.rejects(recoverArtifact(file,'123'),/digest mismatch/);
}));
