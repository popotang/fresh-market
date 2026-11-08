import {readFile,open,link,unlink} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
async function readMaybe(path){try{return await readFile(path);}catch(e){if(e.code==='ENOENT')return null;throw e;}}
export async function recoverArtifact(output,taskId){
  const meta=output+'.meta.json',pending=meta+'.pending';
  const bytes=await readMaybe(output),saved=await readMaybe(meta),staged=await readMaybe(pending);
  if(!bytes){if(saved)throw Error('ARTIFACT_CONFLICT: manifest without file');if(staged)await unlink(pending);return null;}
  if(!saved&&!staged)throw Error('ARTIFACT_CONFLICT: existing file has no authenticated task manifest');
  const info=JSON.parse(String(saved||staged));
  if(info.task_id!==taskId||info.sha256!==hash(bytes)||info.bytes!==bytes.length)throw Error('ARTIFACT_CONFLICT: identity or digest mismatch');
  if(!saved){await link(pending,meta);await unlink(pending);}
  return info;
}
export async function publishArtifact(part,output,result){
  const pending=output+'.meta.json.pending',meta=output+'.meta.json';
  const previous=await recoverArtifact(output,result.task_id);
  if(previous){if(previous.sha256!==result.sha256)throw Error('ARTIFACT_CONFLICT: task changed');await unlink(part);return previous;}
  const manifest=await open(pending,'wx',0o600);
  try{await manifest.writeFile(JSON.stringify(result));await manifest.sync();}finally{await manifest.close();}
  // Staging the manifest first makes a crash after publishing the XLSX recoverable.
  await link(part,output);await unlink(part);await link(pending,meta);await unlink(pending);
  return result;
}
