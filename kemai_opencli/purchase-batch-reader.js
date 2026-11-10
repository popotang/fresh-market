import {readFile, open, link, unlink, lstat} from 'node:fs/promises';
import {dirname, join, isAbsolute} from 'node:path';
import {createHash, randomUUID} from 'node:crypto';

const hash = value => createHash('sha256').update(value).digest('hex');
const kinds = {order:'purchase-orders', receipt:'purchase-receipts', return:'purchase-returns'};
async function publish(path, value) {
  const temp = path+'.'+randomUUID()+'.tmp';
  const handle = await open(temp, 'wx', 0o600);
  try { await handle.writeFile(JSON.stringify(value)); await handle.sync(); }
  finally { await handle.close(); }
  try {
    await link(temp, path); // Never replace completed evidence.
    const directory = await open(dirname(path), 'r');
    try { await directory.sync(); } finally { await directory.close(); }
  } finally { await unlink(temp); }
}

// Dependency injection is for offline verification; production passes the shared gate.
export async function readPurchaseBatch(page, requestPath, {ensurePage, readPreparedPurchase, withSourceLease}) {
  if (!isAbsolute(requestPath)) throw Error('INVALID_ARGUMENT: absolute request file required');
  const dir = dirname(requestPath);
  for (const path of [dir, requestPath]) {
    const stat = await lstat(path);
    if (stat.isSymbolicLink() || (stat.mode & 0o077)) throw Error('PRIVATE_ARTIFACT_REQUIRED');
  }
  const request = JSON.parse(await readFile(requestPath, 'utf8'));
  const items = request.items;
  if (request.schema_version !== 1 || !Object.hasOwn(kinds, request.kind) || !Array.isArray(items)
      || items.length < 1 || items.length > 10) throw Error('INVALID_ARGUMENT: batch size 1..10');
  const ids = new Set(), keys = new Set();
  for (const item of items) {
    if (typeof item.id !== 'string' || !/^\d{1,32}$/.test(item.id)
        || typeof item.key !== 'string' || !/^[a-f0-9]{64}$/.test(item.key)
        || ids.has(item.id) || keys.has(item.key)) throw Error('INVALID_ARGUMENT: duplicate/invalid batch identity');
    ids.add(item.id); keys.add(item.key);
    try { await lstat(join(dir,item.key+'.json')); throw Error('ARTIFACT_EXISTS'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  const started = performance.now();
  const report = {schema_version:1, requested:items.length, completed:0, records:[], outcome:'running'};
  let config;
  try {
    for (const item of items) {
      const timings = {};
      report.records.push(timings);
      await withSourceLease(`kemai:${kinds[request.kind]}:detail`, async () => {
        if (!config) {
          const begin = performance.now();
          config = await ensurePage(page,kinds[request.kind]);
          timings.page_prepare_seconds = (performance.now()-begin)/1000;
        }
        const begin = performance.now();
        const result = await readPreparedPurchase(page,config,item.id);
        timings.browser_detail_seconds = (performance.now()-begin)/1000;
        timings.api_method_seconds = result.api_method_seconds;
        timings.browser_bridge_and_checks_seconds = Math.max(0,timings.browser_detail_seconds-result.api_method_seconds);
        if (String(result.entity?.id) !== item.id) throw Error('SOURCE_IDENTITY_MISMATCH');
        const body = JSON.stringify(result.entity);
        const save = performance.now();
        await publish(join(dir,item.key+'.json'), {schema_version:1, kind:request.kind, id:item.id,
          key:item.key, observed_at:new Date().toISOString(), entity_json:body, sha256:hash(body), timings});
        timings.artifact_write_seconds = (performance.now()-save)/1000;
        report.completed++;
      }, timings);
    }
    report.outcome = 'complete';
    return [report];
  } catch (error) {
    report.outcome = /RATE_LIMIT|频繁|稍后|429/i.test(String(error)) ? 'cooling' : 'failed';
    throw error;
  } finally {
    report.command_seconds = (performance.now()-started)/1000;
    await publish(join(dir,'batch-timing-'+randomUUID()+'.json'),report);
  }
}
