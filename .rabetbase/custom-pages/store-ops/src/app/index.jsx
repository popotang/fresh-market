import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  ConfigProvider,
  DatePicker,
  Empty,
  Skeleton,
  Statistic,
  Table,
  Tag,
  theme as antdTheme,
} from "antd";
import dayjs from "dayjs";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useAppTheme, useI18n, useSdkClient } from "@/context/app-context";
import { locales } from "../locales";
import "./index.css";

echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  TooltipComponent,
  CanvasRenderer,
]);

const SQL_DAILY = "21522951-59797d83";
const SQL_BREAKDOWN = "21522951-65f96f23";
const SQL_STOCK = "21522951-0306b05f";
const WINDOW_DAYS = 14;
const STOCK_WINDOW_DAYS = 7;

const toNumber = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const sumBy = (rows, key) =>
  (Array.isArray(rows) ? rows : []).reduce(
    (total, row) => total + toNumber(row?.[key]),
    0,
  );

const ratio = (value, base) =>
  toNumber(base) > 0 ? (toNumber(value) / toNumber(base)) * 100 : 0;

const safeChange = (current, previous) => {
  if (!toNumber(previous)) return null;
  return ((toNumber(current) - toNumber(previous)) / Math.abs(toNumber(previous))) * 100;
};

const readRows = (result, label) => {
  if (!result || !result.execSuccess) {
    console.error("sql execute failed", label, result);
    throw new Error(`${label} failed`);
  }
  return Array.isArray(result.execResult) ? result.execResult : [];
};

const formatMoney = (value, fractionDigits = 2) =>
  `¥${toNumber(value).toLocaleString("zh-CN", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })}`;

const formatWan = (value) => {
  const amount = toNumber(value);
  if (Math.abs(amount) >= 10000) return `${(amount / 10000).toFixed(1)} 万`;
  return formatMoney(amount, 0);
};

const formatInteger = (value) =>
  toNumber(value).toLocaleString("zh-CN", { maximumFractionDigits: 0 });

const formatPct = (value, digits = 1) => `${toNumber(value).toFixed(digits)}%`;

const getThemeConfig = (appTheme) => ({
  cssVar: { key: "store-ops-theme" },
  algorithm:
    appTheme?.mode === "dark"
      ? antdTheme.darkAlgorithm
      : antdTheme.defaultAlgorithm,
  ...(appTheme?.skinColor ? { token: { colorPrimary: appTheme.skinColor } } : {}),
});

const CACHE_TTL = 10 * 60 * 1000;

const readCache = (key) => {
  if (typeof window === "undefined" || !window.sessionStorage) return null;
  try {
    const raw = window.sessionStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || Date.now() - parsed.ts > CACHE_TTL) return null;
    return parsed.data;
  } catch (cacheError) {
    return null;
  }
};

const writeCache = (key, value) => {
  if (typeof window === "undefined" || !window.sessionStorage) return;
  try {
    window.sessionStorage.setItem(key, JSON.stringify({ ts: Date.now(), data: value }));
  } catch (cacheError) {
    // ignore cache write failures
  }
};

const axisStyle = (token) => ({
  axisLine: { lineStyle: { color: token.colorBorderSecondary } },
  axisLabel: { color: token.colorTextSecondary },
  splitLine: { lineStyle: { color: token.colorBorderSecondary } },
  nameTextStyle: { color: token.colorTextSecondary },
});

const Chart = ({ className = "", option }) => {
  const elementRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    const chart = echarts.init(elementRef.current);
    const resize = () => chart.resize();
    let observer;

    chartRef.current = chart;
    window.addEventListener("resize", resize);
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(resize);
      observer.observe(elementRef.current);
    }

    return () => {
      window.removeEventListener("resize", resize);
      if (observer) observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (chartRef.current && option) {
      chartRef.current.setOption(option, { notMerge: true });
    }
  }, [option]);

  return (
    <div
      className={["ops-chart", className].filter(Boolean).join(" ")}
      ref={elementRef}
    />
  );
};

const createHourlyOption = (rows, $t, token) => {
  const data = [...rows].sort((a, b) => String(a.dim_key).localeCompare(String(b.dim_key)));
  const peak = data.reduce(
    (best, row) => (toNumber(row.sales_amount) > toNumber(best?.sales_amount) ? row : best),
    null,
  );
  return {
    color: [token.colorPrimary, token.colorWarning],
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const point = Array.isArray(params) ? params[0] : params;
        if (!point) return "";
        const row = data[point.dataIndex];
        return `${row.dim_label}<br/>${$t("ops.chart.sales")} ${formatMoney(
          row.sales_amount,
        )}<br/>${$t("ops.chart.orders")} ${formatInteger(row.order_count)}`;
      },
    },
    legend: {
      data: [$t("ops.chart.sales"), $t("ops.chart.orders")],
      textStyle: { color: token.colorTextSecondary },
      top: 0,
    },
    grid: { left: 8, right: 8, top: 44, bottom: 8, containLabel: true },
    xAxis: {
      type: "category",
      data: data.map((row) => row.dim_label),
      ...axisStyle(token),
    },
    yAxis: [
      { type: "value", name: $t("ops.chart.money"), ...axisStyle(token) },
      {
        type: "value",
        name: $t("ops.chart.orders"),
        ...axisStyle(token),
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: $t("ops.chart.sales"),
        type: "bar",
        barMaxWidth: 24,
        data: data.map((row) => ({
          value: toNumber(row.sales_amount),
          itemStyle: {
            borderRadius: [5, 5, 0, 0],
            color: row === peak ? token.colorWarning : token.colorPrimary,
          },
        })),
        markArea: {
          silent: true,
          itemStyle: { color: token.colorFillSecondary },
          label: { color: token.colorTextTertiary, fontSize: 11 },
          data: [
            [
              { name: $t("ops.chart.evening"), xAxis: "17:00" },
              { xAxis: "22:00" },
            ],
          ],
        },
      },
      {
        name: $t("ops.chart.orders"),
        type: "line",
        smooth: true,
        yAxisIndex: 1,
        symbolSize: 6,
        itemStyle: { color: token.colorWarning },
        data: data.map((row) => toNumber(row.order_count)),
      },
    ],
  };
};

const createRankingOption = (rows, $t, token, limit = 10) => {
  const top = [...rows]
    .sort((a, b) => toNumber(b.sales_amount) - toNumber(a.sales_amount))
    .slice(0, limit);
  const reversed = [...top].reverse();
  return {
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const point = Array.isArray(params) ? params[0] : params;
        if (!point) return "";
        const row = reversed[point.dataIndex];
        return `${row.dim_label}<br/>${$t("ops.chart.sales")} ${formatMoney(
          row.sales_amount,
        )}<br/>${$t("ops.chart.orders")} ${formatInteger(row.order_count)}`;
      },
    },
    grid: { left: 8, right: 64, top: 16, bottom: 8, containLabel: true },
    xAxis: { type: "value", name: $t("ops.chart.money"), ...axisStyle(token) },
    yAxis: {
      type: "category",
      data: reversed.map((row) => row.dim_label),
      ...axisStyle(token),
      axisLabel: { color: token.colorText, width: 96, overflow: "truncate" },
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 16,
        data: reversed.map((row) => ({
          value: toNumber(row.sales_amount),
          itemStyle: {
            color: String(row.dim_label).includes("自助")
              ? token.colorInfo
              : token.colorPrimary,
            borderRadius: [0, 5, 5, 0],
          },
        })),
        label: {
          show: true,
          position: "right",
          color: token.colorTextSecondary,
          fontSize: 11,
          formatter: (point) => formatWan(point.value),
        },
      },
    ],
  };
};

const createCategoryOption = (rows, $t, token) => {
  const top = [...rows]
    .sort((a, b) => toNumber(b.sales_amount) - toNumber(a.sales_amount))
    .slice(0, 8);
  return {
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const point = Array.isArray(params) ? params[0] : params;
        if (!point) return "";
        const row = top[point.dataIndex];
        return `${row.dim_label}<br/>${$t("ops.chart.sales")} ${formatMoney(
          row.sales_amount,
        )}<br/>${$t("ops.chart.discount")} ${formatMoney(
          toNumber(row.original_amount) - toNumber(row.sales_amount),
        )}`;
      },
    },
    grid: { left: 8, right: 8, top: 24, bottom: 24, containLabel: true },
    xAxis: {
      type: "category",
      data: top.map((row) => row.dim_label),
      ...axisStyle(token),
      axisLabel: { color: token.colorTextSecondary, interval: 0, rotate: 30 },
    },
    yAxis: { type: "value", name: $t("ops.chart.money"), ...axisStyle(token) },
    series: [
      {
        type: "bar",
        barMaxWidth: 30,
        data: top.map((row) => toNumber(row.sales_amount)),
        itemStyle: { color: token.colorInfo, borderRadius: [5, 5, 0, 0] },
        label: {
          show: true,
          position: "top",
          color: token.colorTextSecondary,
          fontSize: 10,
          formatter: (point) => formatWan(point.value),
        },
      },
    ],
  };
};

const createProductOption = (rows, $t, token) => {
  const top = [...rows]
    .sort((a, b) => toNumber(b.sales_amount) - toNumber(a.sales_amount))
    .slice(0, 10);
  const reversed = [...top].reverse();
  return {
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const point = Array.isArray(params) ? params[0] : params;
        if (!point) return "";
        const row = reversed[point.dataIndex];
        return `${row.dim_label}<br/>${$t("ops.chart.sales")} ${formatMoney(
          row.sales_amount,
        )}<br/>${$t("ops.chart.qty")} ${formatInteger(row.qty)}`;
      },
    },
    grid: { left: 8, right: 64, top: 16, bottom: 8, containLabel: true },
    xAxis: { type: "value", name: $t("ops.chart.money"), ...axisStyle(token) },
    yAxis: {
      type: "category",
      data: reversed.map((row) => row.dim_label),
      ...axisStyle(token),
      axisLabel: { color: token.colorText, width: 130, overflow: "truncate" },
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 16,
        data: reversed.map((row) => toNumber(row.sales_amount)),
        itemStyle: { color: token.colorSuccess, borderRadius: [0, 5, 5, 0] },
        label: {
          show: true,
          position: "right",
          color: token.colorTextSecondary,
          fontSize: 11,
          formatter: (point) => formatWan(point.value),
        },
      },
    ],
  };
};

const StoreOps = () => {
  const client = useSdkClient();
  const $i18n = useI18n();
  const { token } = antdTheme.useToken();

  const [selectedDate, setSelectedDate] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [extraLoading, setExtraLoading] = useState(false);
  const [error, setError] = useState(false);
  const requestRef = useRef(0);

  $i18n.addLocale(locales);

  const $t = useCallback(
    (key, vars) => {
      const text = $i18n.t(key);
      if (!vars) return text;
      return String(text ?? "").replace(/\{(\w+)\}/g, (match, name) =>
        vars[name] === undefined || vars[name] === null ? match : String(vars[name]),
      );
    },
    [$i18n],
  );

  const businessDate = selectedDate || dayjs().format("YYYY-MM-DD");

  const load = useCallback(
    async (force = false) => {
      const cacheKey = `store-ops:v2:${businessDate}`;
      if (!force) {
        const cached = readCache(cacheKey);
        if (cached?.daily?.length) {
          setData(cached);
          setLoading(false);
          setExtraLoading(false);
          setError(false);
          return;
        }
      }

      const requestId = requestRef.current + 1;
      requestRef.current = requestId;
      setLoading(true);
      setError(false);
      try {
        const day = dayjs(businessDate);
        const dayParams = { startDate: businessDate, endDate: businessDate };
        const windowStart = day
          .subtract(WINDOW_DAYS - 1, "day")
          .format("YYYY-MM-DD");

        const dailyRes = await client.sql.execute({
          sqlCode: SQL_DAILY,
          params: { startDate: windowStart, endDate: businessDate },
        });
        const base = {
          daily: readRows(dailyRes, "daily"),
        };
        if (requestId !== requestRef.current) return;
        setData(base);
        setLoading(false);

        if (!selectedDate && base.daily.length) {
          const latest = base.daily[base.daily.length - 1]?.biz_date;
          if (latest && latest !== businessDate) {
            setSelectedDate(latest);
            return;
          }
        }

        setExtraLoading(true);
        const stockParams = {
          startDate: day.subtract(STOCK_WINDOW_DAYS - 1, "day").format("YYYY-MM-DD"),
          endDate: businessDate,
        };
        const [hourRes, cashierRes, categoryRes, itemRes, stockRes] =
          await Promise.all([
            client.sql.execute({
              sqlCode: SQL_BREAKDOWN,
              params: { ...dayParams, dimension: "hour" },
            }),
            client.sql.execute({
              sqlCode: SQL_BREAKDOWN,
              params: { ...dayParams, dimension: "cashier" },
            }),
            client.sql.execute({
              sqlCode: SQL_BREAKDOWN,
              params: { ...dayParams, dimension: "category" },
            }),
            client.sql.execute({
              sqlCode: SQL_BREAKDOWN,
              params: { ...dayParams, dimension: "item" },
            }),
            client.sql.execute({ sqlCode: SQL_STOCK, params: stockParams }),
          ]);
        const full = {
          ...base,
          hourly: readRows(hourRes, "hour"),
          cashiers: readRows(cashierRes, "cashier"),
          categories: readRows(categoryRes, "category"),
          items: readRows(itemRes, "item"),
          stocks: readRows(stockRes, "stock"),
        };
        if (requestId !== requestRef.current) return;
        setData(full);
        writeCache(cacheKey, full);
      } catch (loadError) {
        if (requestId !== requestRef.current) return;
        console.error("load store operations failed", loadError);
        setError(true);
      } finally {
        if (requestId === requestRef.current) {
          setLoading(false);
          setExtraLoading(false);
        }
      }
    },
    [businessDate, client, selectedDate],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const view = useMemo(() => {
    if (!data) return null;
    const daily = [...data.daily].sort((a, b) =>
      String(a.biz_date).localeCompare(String(b.biz_date)),
    );
    const today = daily.find((row) => row.biz_date === businessDate) || null;
    const lastWeekDate = dayjs(businessDate).subtract(7, "day").format("YYYY-MM-DD");
    const lastWeek = daily.find((row) => row.biz_date === lastWeekDate) || null;
    const latestDate = daily[daily.length - 1]?.biz_date || businessDate;

    const sales = toNumber(today?.sales_amount);
    const orders = toNumber(today?.order_count);
    const categories = data.categories || [];
    const cashiers = data.cashiers || [];
    const items = data.items || [];
    const stocks = data.stocks || [];
    const original = sumBy(categories, "original_amount");
    const discount = original ? Math.max(0, original - sales) : 0;
    const memberSales = toNumber(today?.member_sales);
    const memberOrders = toNumber(today?.member_orders);

    const hourly = [...(data.hourly || [])];
    const peak = hourly.reduce(
      (best, row) => (toNumber(row.sales_amount) > toNumber(best?.sales_amount) ? row : best),
      null,
    );
    const eveningSales = hourly
      .filter((row) => toNumber(row.dim_key) >= 17 && toNumber(row.dim_key) <= 21)
      .reduce((total, row) => total + toNumber(row.sales_amount), 0);

    const selfSales = cashiers
      .filter((row) => String(row.dim_label).includes("自助"))
      .reduce((total, row) => total + toNumber(row.sales_amount), 0);
    const cashierSales = Math.max(0, sumBy(cashiers, "sales_amount") - selfSales);

    const stockRisk = stocks.filter(
      (row) => row.risk_level === "stockout" || row.risk_level === "low",
    );
    const slowMovers = stocks
      .filter((row) => row.risk_level === "slow")
      .sort((a, b) => toNumber(b.stock_value) - toNumber(a.stock_value));

    const categoryDiscount = [...categories]
      .map((row) => ({
        ...row,
        discount: Math.max(0, toNumber(row.original_amount) - toNumber(row.sales_amount)),
      }))
      .sort((a, b) => b.discount - a.discount);

    const topProducts = [...items]
      .sort((a, b) => toNumber(b.sales_amount) - toNumber(a.sales_amount))
      .slice(0, 10);

    const actions = [];
    const stockoutRows = stockRisk.filter((row) => row.risk_level === "stockout");
    const lowRows = stockRisk.filter((row) => row.risk_level === "low");
    if (stockoutRows.length) {
      actions.push({
        key: "stockout",
        color: "red",
        text: $t("ops.action.stockout", {
          count: formatInteger(stockoutRows.length),
          names: stockoutRows
            .slice(0, 3)
            .map((row) => row.item_name)
            .join("、"),
        }),
      });
    }
    if (lowRows.length) {
      actions.push({
        key: "low",
        color: "orange",
        text: $t("ops.action.low", { count: formatInteger(lowRows.length) }),
      });
    }
    if (slowMovers.length) {
      actions.push({
        key: "slow",
        color: "default",
        text: $t("ops.action.slow", {
          count: formatInteger(slowMovers.length),
          amount: formatWan(sumBy(slowMovers, "stock_value")),
        }),
      });
    }
    if (peak) {
      actions.push({
        key: "peak",
        color: "blue",
        text: $t("ops.action.peak", {
          hour: peak.dim_label,
          amount: formatMoney(peak.sales_amount),
        }),
      });
    }
    if (categoryDiscount[0]?.discount > 0) {
      actions.push({
        key: "discount",
        color: "gold",
        text: $t("ops.action.discount", {
          rate: formatPct(ratio(discount, original)),
          category: categoryDiscount[0].dim_label,
          amount: formatMoney(categoryDiscount[0].discount),
        }),
      });
    }
    if (sales > 0) {
      actions.push({
        key: "checkout",
        color: "cyan",
        text: $t("ops.action.checkout", {
          self: formatPct(ratio(selfSales, sales)),
          cashier: formatPct(ratio(cashierSales, sales)),
        }),
      });
    }

    return {
      daily,
      today,
      lastWeek,
      latestDate,
      sales,
      orders,
      qty: toNumber(today?.qty),
      basket: orders ? sales / orders : 0,
      lastWeekSales: toNumber(lastWeek?.sales_amount),
      lastWeekOrders: toNumber(lastWeek?.order_count),
      salesChange: safeChange(sales, lastWeek?.sales_amount),
      ordersChange: safeChange(orders, lastWeek?.order_count),
      memberSales,
      memberOrders,
      guestSales: Math.max(0, sales - memberSales),
      discount,
      discountRate: ratio(discount, original),
      hourly,
      eveningShare: ratio(eveningSales, sales),
      peak,
      selfSales,
      cashierSales,
      stockRisk,
      slowMovers,
      topProducts,
      actions,
      extrasLoaded: Boolean(data.stocks),
    };
  }, [data, businessDate, $t]);

  const hourlyOption = useMemo(
    () => (view ? createHourlyOption(view.hourly, $t, token) : null),
    [view, $t, token],
  );
  const cashierOption = useMemo(
    () => (view ? createRankingOption(data?.cashiers || [], $t, token) : null),
    [data, view, $t, token],
  );
  const categoryOption = useMemo(
    () => (view ? createCategoryOption(data?.categories || [], $t, token) : null),
    [data, view, $t, token],
  );
  const productOption = useMemo(
    () => (view ? createProductOption(data?.items || [], $t, token) : null),
    [data, view, $t, token],
  );

  const replenishColumns = [
    { title: $t("ops.table.item"), dataIndex: "item_name", ellipsis: true },
    {
      title: $t("ops.table.stock"),
      dataIndex: "stock_qty",
      align: "right",
      render: (value) => formatInteger(value),
    },
    {
      title: $t("ops.table.sold"),
      dataIndex: "sold_qty",
      align: "right",
      render: (value) => formatInteger(value),
    },
    {
      title: $t("ops.table.cover"),
      dataIndex: "days_cover",
      align: "right",
      render: (value, row) =>
        row.risk_level === "stockout" ? $t("ops.stock.out") : toNumber(value).toFixed(1),
    },
    {
      title: $t("ops.table.suggest"),
      dataIndex: "sold_qty",
      align: "right",
      render: (value, row) =>
        formatInteger(Math.max(0, Math.ceil(toNumber(value) - toNumber(row.stock_qty)) + toNumber(value))),
    },
    {
      title: $t("ops.table.status"),
      dataIndex: "risk_level",
      render: (value) => (
        <Tag color={value === "stockout" ? "red" : "orange"}>
          {value === "stockout" ? $t("ops.stock.out") : $t("ops.stock.low")}
        </Tag>
      ),
    },
  ];

  const slowColumns = [
    { title: $t("ops.table.item"), dataIndex: "item_name", ellipsis: true },
    {
      title: $t("ops.table.stock"),
      dataIndex: "stock_qty",
      align: "right",
      render: (value) => formatInteger(value),
    },
    {
      title: $t("ops.table.stockValue"),
      dataIndex: "stock_value",
      align: "right",
      render: (value) => formatMoney(value, 0),
    },
    {
      title: $t("ops.table.lastSold"),
      dataIndex: "last_sold_at",
      render: (value) => (value ? dayjs(value).format("MM-DD") : $t("ops.table.never")),
    },
  ];

  return (
    <main className="ops-container">
      <header className="ops-header">
        <div className="ops-header__main">
          <h1 className="ops-header__title">{$t("ops.title")}</h1>
          <p className="ops-header__subtitle">
            {$t("ops.subtitle", {
              date: businessDate,
              latest: view?.latestDate || "-",
              warehouse: view?.today?.branch_name || "-",
            })}
          </p>
        </div>
        <div className="ops-header__actions">
          <DatePicker
            allowClear={false}
            value={dayjs(businessDate)}
            onChange={(value) => {
              if (value) setSelectedDate(value.format("YYYY-MM-DD"));
            }}
          />
          <Button loading={loading} onClick={() => void load(true)}>
            {$t("ops.action.reload")}
          </Button>
        </div>
      </header>
      <p className="ops-header__hint">{$t("ops.hint")}</p>

      {error ? (
        <Alert
          action={
            <Button size="small" onClick={() => void load(true)}>
              {$t("ops.action.retry")}
            </Button>
          }
          message={$t("ops.error.title")}
          description={$t("ops.error.desc")}
          showIcon
          type="error"
        />
      ) : null}

      {!view ? (
        loading ? (
          <div className="ops-loading">
            <Skeleton active paragraph={{ rows: 10 }} />
          </div>
        ) : error ? null : (
          <Empty description={$t("ops.empty")} />
        )
      ) : (
        <>
          <section className="ops-actions">
            <h2 className="ops-actions__title">{$t("ops.actions.title")}</h2>
            <div className="ops-actions__list">
              {view.extrasLoaded ? (
                view.actions.map((action) => (
                  <Tag className="ops-action" color={action.color} key={action.key}>
                    {action.text}
                  </Tag>
                ))
              ) : (
                <span className="ops-actions__loading">{$t("ops.actions.loading")}</span>
              )}
            </div>
          </section>

          <section className="ops-kpis">
            <Card className="ops-kpi" classNames={{ body: "ops-kpi__body" }}>
              <Statistic
                title={$t("ops.kpi.sales")}
                value={view.sales}
                precision={2}
                prefix="¥"
              />
              <span className="ops-kpi__hint">
                {$t("ops.kpi.vsLastWeek", {
                  value:
                    view.salesChange === null
                      ? "-"
                      : `${view.salesChange >= 0 ? "+" : ""}${view.salesChange.toFixed(1)}%`,
                })}
              </span>
            </Card>
            <Card className="ops-kpi" classNames={{ body: "ops-kpi__body" }}>
              <Statistic title={$t("ops.kpi.orders")} value={view.orders} />
              <span className="ops-kpi__hint">
                {$t("ops.kpi.vsLastWeek", {
                  value:
                    view.ordersChange === null
                      ? "-"
                      : `${view.ordersChange >= 0 ? "+" : ""}${view.ordersChange.toFixed(1)}%`,
                })}
              </span>
            </Card>
            <Card className="ops-kpi" classNames={{ body: "ops-kpi__body" }}>
              <Statistic
                title={$t("ops.kpi.basket")}
                value={view.basket}
                precision={2}
                prefix="¥"
              />
              <span className="ops-kpi__hint">
                {$t("ops.kpi.basketHint", { qty: formatInteger(view.qty) })}
              </span>
            </Card>
            <Card className="ops-kpi" classNames={{ body: "ops-kpi__body" }}>
              <Statistic
                title={$t("ops.kpi.member")}
                value={ratio(view.memberSales, view.sales)}
                precision={1}
                suffix="%"
              />
              <span className="ops-kpi__hint">
                {$t("ops.kpi.memberHint", { count: formatInteger(view.memberOrders) })}
              </span>
            </Card>
            <Card className="ops-kpi" classNames={{ body: "ops-kpi__body" }}>
              <Statistic
                title={$t("ops.kpi.discount")}
                value={view.discountRate}
                precision={1}
                suffix="%"
              />
              <span className="ops-kpi__hint">
                {$t("ops.kpi.discountHint", { amount: formatMoney(view.discount, 0) })}
              </span>
            </Card>
            <Card className="ops-kpi" classNames={{ body: "ops-kpi__body" }}>
              <Statistic
                title={$t("ops.kpi.evening")}
                value={view.eveningShare}
                precision={1}
                suffix="%"
              />
              <span className="ops-kpi__hint">
                {$t("ops.kpi.eveningHint", { hour: view.peak?.dim_label || "-" })}
              </span>
            </Card>
          </section>

          <section className="ops-grid">
            <Card className="ops-card ops-span-2" title={$t("ops.chart.hourlyTitle")}>
              <p className="ops-card__desc">
                {$t("ops.chart.hourlyDesc", {
                  evening: formatPct(view.eveningShare),
                  hour: view.peak?.dim_label || "-",
                })}
              </p>
              {view.extrasLoaded ? (
                <Chart className="ops-chart--main" option={hourlyOption} />
              ) : (
                <Skeleton active paragraph={{ rows: 6 }} />
              )}
            </Card>

            <Card className="ops-card" title={$t("ops.chart.cashierTitle")}>
              <p className="ops-card__desc">{$t("ops.chart.cashierDesc")}</p>
              {view.extrasLoaded ? (
                <Chart className="ops-chart--normal" option={cashierOption} />
              ) : (
                <Skeleton active paragraph={{ rows: 5 }} />
              )}
            </Card>

            <Card className="ops-card" title={$t("ops.chart.categoryTitle")}>
              <p className="ops-card__desc">{$t("ops.chart.categoryDesc")}</p>
              {view.extrasLoaded ? (
                <Chart className="ops-chart--normal" option={categoryOption} />
              ) : (
                <Skeleton active paragraph={{ rows: 5 }} />
              )}
            </Card>

            <Card className="ops-card ops-span-2" title={$t("ops.chart.productTitle")}>
              <p className="ops-card__desc">{$t("ops.chart.productDesc")}</p>
              {view.extrasLoaded ? (
                <Chart className="ops-chart--normal" option={productOption} />
              ) : (
                <Skeleton active paragraph={{ rows: 5 }} />
              )}
            </Card>

            <Card className="ops-card ops-span-2" title={$t("ops.table.replenishTitle")}>
              <p className="ops-card__desc">{$t("ops.table.replenishDesc")}</p>
              {view.extrasLoaded ? (
                <Table
                  columns={replenishColumns}
                  dataSource={view.stockRisk.slice(0, 12)}
                  pagination={false}
                  rowKey={(row) => `${row.branch_id}-${row.item_id}`}
                  size="small"
                />
              ) : (
                <Skeleton active paragraph={{ rows: 5 }} />
              )}
            </Card>

            <Card className="ops-card ops-span-2" title={$t("ops.table.slowTitle")}>
              <p className="ops-card__desc">{$t("ops.table.slowDesc")}</p>
              {view.extrasLoaded ? (
                <Table
                  columns={slowColumns}
                  dataSource={view.slowMovers.slice(0, 10)}
                  pagination={false}
                  rowKey={(row) => `${row.branch_id}-${row.item_id}`}
                  size="small"
                />
              ) : (
                <Skeleton active paragraph={{ rows: 5 }} />
              )}
            </Card>
          </section>

          <footer className="ops-footer">
            <h3 className="ops-footer__title">{$t("ops.footer.title")}</h3>
            <p>{$t("ops.footer.scope", { date: businessDate })}</p>
            <p>{$t("ops.footer.stock")}</p>
            <p>{$t("ops.footer.noWrite")}</p>
            <p>{$t("ops.footer.generated", { time: dayjs().format("YYYY-MM-DD HH:mm") })}</p>
          </footer>
        </>
      )}
    </main>
  );
};

const App = () => {
  const $i18n = useI18n();
  const appTheme = useAppTheme();
  const themeConfig = useMemo(() => getThemeConfig(appTheme), [appTheme?.mode, appTheme?.skinColor]);

  $i18n.addLocale(locales);

  return (
    <ConfigProvider theme={themeConfig}>
      <StoreOps />
    </ConfigProvider>
  );
};

export default App;
