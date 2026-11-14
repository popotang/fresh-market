-- @lovrabet.sqlCode: 21522951-99bdbabf
-- @lovrabet.sqlName: metricCacheDelete
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql
-- 受控缓存维护：只删除当前查询作用域，允许重算重建，不触碰业务事实表。
DELETE FROM metric_values
WHERE cache_key = #{cacheKey}
