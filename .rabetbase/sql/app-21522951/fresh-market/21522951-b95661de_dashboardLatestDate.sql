-- @lovrabet.sqlCode: 21522951-b95661de
-- @lovrabet.sqlName: dashboardLatestDate
-- @lovrabet.dbId: 10510
-- @lovrabet.dbName: fresh-market
-- @lovrabet.mode: sql

SELECT DATE_FORMAT(MAX(f.sold_at), '%Y-%m-%d') AS latest_date
FROM retail_sale_flows f
