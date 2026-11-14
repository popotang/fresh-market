import test from 'node:test';
import assert from 'node:assert/strict';
import {cp, mkdtemp, mkdir, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const {listRequest, registrations} = await (async () => {
  const directory = await mkdtemp(join(tmpdir(), 'kemai-product-incremental-'));
  try {
    const adapter = join(directory, 'kemai_opencli');
    await cp(join(root, 'kemai_opencli'), adapter, {recursive: true});
    await writeFile(join(directory, 'package.json'), JSON.stringify({type: 'module'}));
    const stub = join(directory, 'node_modules', '@jackwener', 'opencli');
    await mkdir(stub, {recursive: true});
    await writeFile(join(stub, 'package.json'), JSON.stringify({
      name: '@jackwener/opencli', version: '2.0.3', type: 'module',
      exports: {'./errors': './errors.js', './registry': './registry.js'},
    }));
    await writeFile(join(stub, 'errors.js'), `
      export class CliError extends Error {
        constructor(code, message) { super(message); this.code = code; }
      }
    `);
    await writeFile(join(stub, 'registry.js'), `
      export const registrations = [];
      export const Strategy = {COOKIE: 'cookie'};
      export function cli(definition) { registrations.push(definition); }
    `);
    const reader = await import(pathToFileURL(join(adapter, 'product-reader.js')));
    const {registrations} = await import(pathToFileURL(join(stub, 'registry.js')));
    await import(pathToFileURL(join(adapter, 'product-list.js')));
    return {...reader, registrations};
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
})();

test('product list builds a create-time incremental request with evidence', () => {
  const request = listRequest({from: '2026-11-09', sort: 'gmtCreate', page: 2, rows: 100});
  assert.deepEqual(request.params, {startGmtCreate: '2026-11-09 00:00:00'});
  assert.deepEqual(request.orders, [{fieldName: 'gmtCreate', order: 'desc', frontendSort: false}]);
  assert.deepEqual(request.query_scope, {
    params: {startGmtCreate: '2026-11-09 00:00:00'},
    orders: [{fieldName: 'gmtCreate', order: 'desc', frontendSort: false}],
  });
  assert.equal(request.page, 2);
});

test('product list builds an approve-time incremental request', () => {
  const request = listRequest({from: '2026-11-09 08:30:00', sort: 'approveTime'});
  assert.deepEqual(request.params, {startApproveTime: '2026-11-09 08:30:00'});
  assert.equal(request.orders[0].fieldName, 'approveTime');
});

test('product list default request keeps API default scope', () => {
  const request = listRequest({page: 1, rows: 100});
  assert.deepEqual(request.params, {});
  assert.deepEqual(request.orders, []);
  assert.deepEqual(request.query_scope, {});
});

test('product list rejects unsupported sort and malformed time', () => {
  assert.throws(() => listRequest({from: '2026-11-09', sort: 'gmtModified'}), /sort/);
  assert.throws(() => listRequest({from: '11/09/2026', sort: 'gmtCreate'}), /from/);
});

test('product-list registration exposes incremental arguments', () => {
  const definition = registrations.find(command => command.name === 'product-list');
  assert.ok(definition);
  const names = definition.args.map(argument => argument.name);
  assert.ok(names.includes('from'));
  assert.ok(names.includes('sort'));
});
