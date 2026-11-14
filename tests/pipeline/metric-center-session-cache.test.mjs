import test from 'node:test';
import assert from 'node:assert/strict';
import { createSessionCache } from '../../.rabetbase/custom-pages/metric-center/src/lib/sessionCache.js';

const makeStorage = () => {
  const data = new Map();
  return {
    get length() { return data.size; },
    key: (index) => [...data.keys()][index],
    getItem: (key) => data.has(key) ? data.get(key) : null,
    setItem: (key, value) => data.set(key, value),
    removeItem: (key) => data.delete(key),
  };
};

test('session cache returns fresh values and expires old values', () => {
  const storage = makeStorage();
  let now = 1000;
  const cache = createSessionCache({ storage, prefix: 'metric-center', ttlMs: 600000, now: () => now });
  cache.write('query:a', { value: 42 });
  assert.deepEqual(cache.read('query:a'), { value: 42 });
  now += 600001;
  assert.equal(cache.read('query:a'), null);
  assert.equal(storage.getItem('metric-center:query:a'), null);
});

test('clear removes only this page cache and tolerates invalid entries', () => {
  const storage = makeStorage();
  storage.setItem('metric-center:bad', '{bad json');
  storage.setItem('other:key', 'keep');
  const cache = createSessionCache({ storage, prefix: 'metric-center', ttlMs: 600000, now: () => 1000 });
  assert.equal(cache.read('bad'), null);
  cache.write('query:a', { value: 1 });
  assert.deepEqual(cache.keys(), ['bad', 'query:a']);
  cache.clear();
  assert.equal(storage.getItem('metric-center:query:a'), null);
  assert.equal(storage.getItem('other:key'), 'keep');
});

test('storage failures do not break the page', () => {
  const storage = {
    get length() { return 0; },
    key: () => null,
    getItem: () => { throw new Error('denied'); },
    setItem: () => { throw new Error('quota'); },
    removeItem: () => { throw new Error('denied'); },
  };
  const cache = createSessionCache({ storage, prefix: 'x', ttlMs: 1, now: () => 1 });
  assert.equal(cache.read('x'), null);
  assert.doesNotThrow(() => cache.write('x', { a: 1 }));
  assert.doesNotThrow(() => cache.clear());
});
