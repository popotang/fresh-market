export function createSessionCache({ storage, prefix, ttlMs = 600000, now = () => Date.now() }) {
  const fullKey = (key) => `${prefix}:${key}`;
  const available = () => Boolean(storage);

  const read = (key) => {
    if (!available()) return null;
    try {
      const raw = storage.getItem(fullKey(key));
      if (!raw) return null;
      const entry = JSON.parse(raw);
      if (!entry || typeof entry.storedAt !== 'number') return null;
      if (now() - entry.storedAt >= ttlMs) {
        storage.removeItem(fullKey(key));
        return null;
      }
      return entry.value;
    } catch {
      return null;
    }
  };

  const write = (key, value) => {
    if (!available()) return;
    try {
      storage.setItem(fullKey(key), JSON.stringify({ storedAt: now(), value }));
    } catch {
      // Quota/privacy errors must not block the underlying data query.
    }
  };

  const keys = () => {
    if (!available()) return [];
    const result = [];
    try {
      for (let index = 0; index < storage.length; index += 1) {
        const key = storage.key(index);
        if (key && key.startsWith(`${prefix}:`)) result.push(key.slice(prefix.length + 1));
      }
    } catch {
      return [];
    }
    return result;
  };

  const clear = () => {
    if (!available()) return;
    for (const key of keys()) {
      try {
        storage.removeItem(fullKey(key));
      } catch {
        // Best-effort cache reset only.
      }
    }
  };

  return { read, write, keys, clear };
}
