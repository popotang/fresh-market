import test from 'node:test';
import assert from 'node:assert/strict';
import {cp, mkdtemp, mkdir, readFile, rm, writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const adapterSource = join(root, 'kemai_opencli');
const expectedCommands = [
  'capabilities',
  'capture-page',
  'export-download',
  'export-task-list',
  'item-archives-export-start',
  'master-list',
  'product-detail',
  'product-list',
  'product-section',
  'purchase-detail',
  'purchase-detail-batch',
  'purchase-orders',
  'purchase-receipts',
  'purchase-receive-history',
  'purchase-receive-history-export-start',
  'purchase-returns',
  'query-scope',
  'retail-order-detail',
  'retail-orders',
  'retail-orders-export-start',
  'retail-promote-sharing',
  'retail-sales',
  'retail-sales-export-start',
];

async function fixture(run) {
  const directory = await mkdtemp(join(tmpdir(), 'kemai-opencli-registration-'));
  try {
    const adapter = join(directory, 'kemai_opencli');
    await cp(adapterSource, adapter, {recursive: true});
    await writeFile(join(directory, 'package.json'), JSON.stringify({type: 'module'}));
    const stub = join(directory, 'node_modules', '@jackwener', 'opencli');
    await mkdir(stub, {recursive: true});
    await writeFile(join(stub, 'package.json'), JSON.stringify({
      name: '@jackwener/opencli',
      version: '1.8.8',
      type: 'module',
      exports: {'./registry': './registry.js', './errors': './errors.js'},
    }));
    await writeFile(join(stub, 'registry.js'), `
      export const registrations = [];
      export const Strategy = {COOKIE: 'cookie'};
      export function cli(definition) { registrations.push(definition); }
    `);
    await writeFile(join(stub, 'errors.js'), `
      export class CliError extends Error {
        constructor(code, message) { super(message); this.code = code; }
      }
    `);
    const {registrations} = await import(pathToFileURL(join(stub, 'registry.js')));
    const manifest = JSON.parse(await readFile(join(adapter, 'manifest.json'), 'utf8'));
    return await run({adapter, manifest, registrations});
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
}

test('manifest declares the final safe command and side-effect contract', async () => {
  const manifest = JSON.parse(await readFile(join(adapterSource, 'manifest.json'), 'utf8'));
  assert.equal(manifest.schema_version, 1);
  assert.equal(manifest.adapter, 'kemai');
  assert.ok(manifest.adapter_version);
  assert.ok(manifest.runtime_requirements.opencli_version);
  assert.ok(manifest.limitations.length > 0);
  assert.deepEqual(manifest.commands.map(command => command.name).sort(), expectedCommands);
  assert.equal(new Set(manifest.commands.map(command => command.name)).size, 23);
  for (const command of manifest.commands) {
    assert.equal(command.access, 'read');
    assert.equal(command.output_contract.type, 'row_array');
    assert.ok(Array.isArray(command.output_contract.columns));
    assert.ok(!command.output_contract.columns.includes('sourceUrl'), command.name);
    assert.equal(typeof command.creates_export_task, 'boolean', command.name);
    assert.equal(typeof command.local_download, 'boolean', command.name);
  }
  assert.deepEqual(
    manifest.commands.filter(command => command.creates_export_task).map(command => command.name).sort(),
    ['item-archives-export-start', 'purchase-receive-history-export-start', 'retail-orders-export-start', 'retail-sales-export-start'],
  );
  assert.deepEqual(manifest.commands.filter(command => command.local_download).map(command => command.name), ['export-download']);
  const names=new Set(manifest.commands.map(command=>command.name));
  for(const transport of Object.values(manifest.source_transports)) {
    for(const [field,value] of Object.entries(transport)) {
      if(field.endsWith('_command') && value!==null)assert.ok(names.has(value),field);
    }
  }
  assert.equal(manifest.source_transports['purchase-orders'].export_start_command,null);
  assert.equal(manifest.source_transports['purchase-receive-history'].export_start_command,null);
});

test('static capabilities succeed with a throwing page and zero source access', async () => fixture(async ({adapter, manifest, registrations}) => {
  await import(pathToFileURL(join(adapter, 'capabilities.js')));
  assert.equal(registrations.length, 1);
  const capability = registrations[0];
  assert.equal(capability.name, 'capabilities');
  assert.equal(capability.browser, false);
  assert.ok(!capability.columns.includes('sourceUrl'));
  const page = new Proxy({}, {get() { throw new Error('browser access forbidden'); }});
  const rows = await capability.func(page, {});
  assert.equal(rows.length, 1);
  assert.equal(rows[0].source_requests, 0);
  assert.equal(rows[0].authentication, null);
  assert.equal(rows[0].source_acceptance, 'not_checked');
  assert.equal(rows[0].commands.length, 23);
  assert.deepEqual(rows[0].commands.map(command => command.name).sort(), expectedCommands);
  assert.equal(rows[0].adapter_version, manifest.adapter_version);
}));

const projectionReady = existsSync(join(adapterSource, 'export-task-projection.js'));
test('real module registrations match the manifest once Task 3 projection is complete', {
  skip: projectionReady ? false : 'Task 3 export-task-projection.js is not present yet',
}, async () => fixture(async ({adapter, manifest, registrations}) => {
  for (const command of manifest.commands) {
    await import(pathToFileURL(join(adapter, command.file)));
  }
  assert.equal(registrations.length, 23);
  assert.deepEqual(registrations.map(command => command.name).sort(), expectedCommands);
  assert.equal(new Set(registrations.map(command => command.name)).size, 23);
  const byName = new Map(registrations.map(command => [command.name, command]));
  for (const command of manifest.commands) {
    const registration = byName.get(command.name);
    assert.ok(registration, command.name);
    assert.equal(registration.access, 'read', command.name);
    assert.deepEqual(registration.columns, command.output_contract.columns, command.name);
    assert.ok(!registration.columns.includes('sourceUrl'), command.name);
  }
}));

test('export-start commands stop before the browser or source gate', async () => fixture(async ({adapter, manifest, registrations}) => {
  for (const command of manifest.commands.filter(row => row.creates_export_task)) {
    await import(pathToFileURL(join(adapter, command.file)));
  }
  const page = new Proxy({}, {get() { throw new Error('browser must not be accessed'); }});
  assert.equal(registrations.length, 4);
  for (const command of registrations) {
    await assert.rejects(command.func(page, {}), /SOURCE_WRITE_FORBIDDEN/);
  }
}));
