import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  ConfigProvider,
  DatePicker,
  Empty,
  Segmented,
  Skeleton,
  Statistic,
  Table,
  Tag,
  theme as antdTheme,
} from "antd";
import dayjs from "dayjs";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useAppTheme, useI18n, useSdkClient } from "@/context/app-context";
import { locales } from "../locales";
import "./index.css";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
]);

const SQL_DAILY = "21522951-59797d83";
const SQL_BREAKDOWN = "21522951-65f96f23";
const SQL_PURCHASE = "21522951-95f72415";
const SQL_STOCK = "21522951-0306b05f";

const WEEKDAY_KEYS = [
  "weekday.sun",
  "weekday.mon",
  "weekday.tue",
  "weekday.wed",
  "weekday.thu",
  "weekday.fri",
  "weekday.sat",
];

const toNumber = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const sumBy = (rows, key) =>
  (Array.isArray(rows) ? rows : []).reduce(
    (total, row) => total + toNumber(row?.[key]),
    0,
  );

const ratio = (value, base) => (toNumber(base) > 0 ? (toNumber(value) / toNumber(base)) * 100 : 0);

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
  cssVar: { key: "owner-cockpit-theme" },
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
    // ignore cache write failures (quota / private mode)
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
      className={["cockpit-chart", className].filter(Boolean).join(" ")}
      ref={elementRef}
    />
  );
};

const createTrendOption = (daily, $t, token) => {
  const labels = daily.map((item) => dayjs(item.biz_date).format("MM-DD"));
  return {
    color: [token.colorPrimary, token.colorWarning],
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const rows = Array.isArray(params) ? params : [params];
        if (!rows.length) return "";
        const index = rows[0].dataIndex;
        const row = daily[index];
        return [
          `${row.biz_date} ${$t(WEEKDAY_KEYS[dayjs(row.biz_date).day()])}`,
          `${$t("owner.chart.sales")} ${formatMoney(row.sales_amount)}`,
          `${$t("owner.chart.grossProfit")} ${formatMoney(row.gross_profit)}`,
          `${$t("owner.chart.orders")} ${formatInteger(row.order_count)}`,
        ].join("<br/>");
      },
    },
    legend: {
      data: [$t("owner.chart.sales"), $t("owner.chart.grossProfit")],
      textStyle: { color: token.colorTextSecondary },
      top: 0,
    },
    grid: { left: 8, right: 8, top: 48, bottom: 8, containLabel: true },
    xAxis: {
      type: "category",
      data: labels,
      ...axisStyle(token),
    },
    yAxis: [
      { type: "value", name: $t("owner.chart.money"), ...axisStyle(token) },
      {
        type: "value",
        name: $t("owner.chart.money"),
        ...axisStyle(token),
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: $t("owner.chart.sales"),
        type: "bar",
        barMaxWidth: 26,
        data: daily.map((item) => ({
          value: toNumber(item.sales_amount),
          itemStyle: {
            borderRadius: [5, 5, 0, 0],
            color:
              dayjs(item.biz_date).day() === 0 || dayjs(item.biz_date).day() === 6
                ? token.colorInfo
                : token.colorPrimary,
          },
        })),
      },
      {
        name: $t("owner.chart.grossProfit"),
        type: "line",
        smooth: true,
        yAxisIndex: 1,
        symbolSize: 6,
        itemStyle: { color: token.colorWarning },
        data: daily.map((item) => toNumber(item.gross_profit)),
      },
    ],
  };
};

const createDepartmentPieOption = (rows, $t, token) => {
  const palette = [
    token.colorPrimary,
    token.colorInfo,
    token.colorWarning,
    token.colorSuccess,
    token.gold,
    token.purple,
    token.cyan,
  ].filter(Boolean);
  return {
    tooltip: {
      trigger: "item",
      formatter: (point) =>
        `${point.name}<br/>${formatMoney(point.value)} · ${point.percent}%`,
    },
    legend: {
      bottom: 0,
      textStyle: { color: token.colorTextSecondary },
      itemWidth: 10,
      itemHeight: 10,
    },
    series: [
      {
        type: "pie",
        radius: ["46%", "70%"],
        center: ["50%", "44%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: token.colorBgContainer, borderWidth: 2 },
        label: { show: false },
        data: rows.map((row, index) => ({
          name: row.dim_label || $t("owner.common.unknown"),
          value: toNumber(row.sales_amount),
          itemStyle: { color: palette[index % palette.length] },
        })),
      },
    ],
  };
};

const createMarginOption = (rows, $t, token) => {
  const usable = rows
    .filter((row) => toNumber(row.sales_amount) > 0)
    .map((row) => {
      const coverage = ratio(row.sales_with_cost, row.sales_amount);
      const margin = ratio(row.gross_profit, row.sales_with_cost);
      return { ...row, coverage, margin };
    })
    .sort((a, b) => b.margin - a.margin);
  const reversed = [...usable].reverse();

  return {
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const point = Array.isArray(params) ? params[0] : params;
        if (!point) return "";
        const row = reversed[point.dataIndex];
        if (row.coverage < 20) {
          return `${row.dim_label}<br/>${$t("owner.chart.costCoverage")} ${formatPct(
            row.coverage,
          )}<br/>${$t("owner.chart.marginMissing")}`;
        }
        return `${row.dim_label}<br/>${$t("owner.chart.marginRate")} ${formatPct(
          row.margin,
        )}<br/>${$t("owner.chart.costCoverage")} ${formatPct(row.coverage)}`;
      },
    },
    grid: { left: 8, right: 56, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: "value",
      name: "%",
      max: 40,
      ...axisStyle(token),
    },
    yAxis: {
      type: "category",
      data: reversed.map((row) => row.dim_label),
      ...axisStyle(token),
      axisLabel: { color: token.colorText, width: 90, overflow: "truncate" },
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 18,
        data: reversed.map((row) => ({
          value: row.coverage < 20 ? 0 : Number(row.margin.toFixed(1)),
          itemStyle: {
            color: row.coverage < 20 ? token.colorFillSecondary : token.colorSuccess,
            borderRadius: [0, 5, 5, 0],
          },
        })),
        label: {
          show: true,
          position: "right",
          color: token.colorTextSecondary,
          fontSize: 11,
          formatter: (point) => {
            const row = reversed[point.dataIndex];
            if (row.coverage < 20) return $t("owner.chart.noCost");
            return formatPct(row.margin);
          },
        },
      },
    ],
  };
};

const createWeekdayOption = (rows, $t, token) => ({
  tooltip: {
    trigger: "axis",
    formatter: (params) => {
      const point = Array.isArray(params) ? params[0] : params;
      if (!point) return "";
      return `${point.name}<br/>${$t("owner.chart.avgSales")} ${formatMoney(
        point.value,
      )}`;
    },
  },
  grid: { left: 8, right: 8, top: 16, bottom: 8, containLabel: true },
  xAxis: {
    type: "category",
    data: rows.map((row) => row.label),
    ...axisStyle(token),
  },
  yAxis: { type: "value", name: $t("owner.chart.money"), ...axisStyle(token) },
  series: [
    {
      type: "bar",
      barMaxWidth: 34,
      data: rows.map((row) => ({
        value: toNumber(row.value),
        itemStyle: {
          borderRadius: [6, 6, 0, 0],
          color: row.weekend ? token.colorInfo : token.colorPrimary,
        },
      })),
      label: {
        show: true,
        position: "top",
        color: token.colorTextSecondary,
        fontSize: 10,
        formatter: (point) => formatWan(point.value),
      },
    },
  ],
});

const createSupplierOption = (rows, $t, token) => {
  const top = rows.slice(0, 10);
  const reversed = [...top].reverse();
  const total = sumBy(rows, "net_amount");
  let running = 0;
  const cumulative = new Map();
  [...rows]
    .sort((a, b) => toNumber(b.net_amount) - toNumber(a.net_amount))
    .forEach((row) => {
      running += toNumber(row.net_amount);
      cumulative.set(row.supplier_id, total > 0 ? (running / total) * 100 : 0);
    });

  return {
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const list = Array.isArray(params) ? params : [params];
        const bar = list.find((item) => item.seriesType === "bar");
        if (!bar) return "";
        const row = reversed[bar.dataIndex];
        return `${row.supplier_name}<br/>${$t("owner.chart.purchaseNet")} ${formatMoney(
          row.net_amount,
        )}<br/>${$t("owner.chart.cumulative")} ${formatPct(
          cumulative.get(row.supplier_id),
        )}`;
      },
    },
    grid: { left: 8, right: 56, top: 16, bottom: 8, containLabel: true },
    xAxis: {
      type: "value",
      name: $t("owner.chart.money"),
      ...axisStyle(token),
    },
    yAxis: {
      type: "category",
      data: reversed.map((row) =>
        String(row.supplier_name || "").replace(/有限公司|股份/g, ""),
      ),
      ...axisStyle(token),
      axisLabel: { color: token.colorText, width: 120, overflow: "truncate" },
    },
    series: [
      {
        type: "bar",
        barMaxWidth: 16,
        data: reversed.map((row) => ({
          value: toNumber(row.net_amount),
          itemStyle: { color: token.colorInfo, borderRadius: [0, 5, 5, 0] },
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

const OwnerCockpit = () => {
  const client = useSdkClient();
  const $i18n = useI18n();
  const { token } = antdTheme.useToken();

  const [range, setRange] = useState(() => [
    dayjs().subtract(29, "day").startOf("day"),
    dayjs().startOf("day"),
  ]);
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [extraLoading, setExtraLoading] = useState(false);
  const [error, setError] = useState(false);
  const requestRef = useRef(0);
  const anchoredRef = useRef(false);

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

  const startDate = range[0].format("YYYY-MM-DD");
  const endDate = range[1].format("YYYY-MM-DD");

  const load = useCallback(
    async (force = false) => {
      const cacheKey = `owner-cockpit:v2:${startDate}:${endDate}`;
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
        const params = { startDate, endDate };
        const dailyRes = await client.sql.execute({ sqlCode: SQL_DAILY, params });
        const base = {
          daily: readRows(dailyRes, "daily"),
        };
        if (requestId !== requestRef.current) return;
        setData(base);
        setLoading(false);
        setExtraLoading(true);

        const stockParams = {
          startDate: dayjs(endDate).subtract(6, "day").format("YYYY-MM-DD"),
          endDate,
        };
        const [deptRes, supplierRes, stockRes] = await Promise.all([
          client.sql.execute({
            sqlCode: SQL_BREAKDOWN,
            params: { ...params, dimension: "department" },
          }),
          client.sql.execute({ sqlCode: SQL_PURCHASE, params }),
          client.sql.execute({ sqlCode: SQL_STOCK, params: stockParams }),
        ]);
        const full = {
          ...base,
          departments: readRows(deptRes, "department"),
          suppliers: readRows(supplierRes, "supplier"),
          stocks: readRows(stockRes, "stock"),
        };
        if (requestId !== requestRef.current) return;
        setData(full);
        writeCache(cacheKey, full);
      } catch (loadError) {
        if (requestId !== requestRef.current) return;
        console.error("load owner cockpit failed", loadError);
        setError(true);
      } finally {
        if (requestId === requestRef.current) {
          setLoading(false);
          setExtraLoading(false);
        }
      }
    },
    [client, endDate, startDate],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const view = useMemo(() => {
    if (!data || !data.daily.length) return null;
    const daily = [...data.daily].sort((a, b) =>
      String(a.biz_date).localeCompare(String(b.biz_date)),
    );
    const departments = data.departments || [];
    const suppliersRaw = data.suppliers || [];
    const stocks = data.stocks || [];

    const totals = {
      sales: sumBy(daily, "sales_amount"),
      orders: sumBy(daily, "order_count"),
      qty: sumBy(daily, "qty"),
      refund: sumBy(daily, "refund_amount"),
      memberSales: sumBy(daily, "member_sales"),
      memberOrders: sumBy(daily, "member_orders"),
      grossProfit: sumBy(daily, "gross_profit"),
      salesWithCost: sumBy(daily, "sales_with_cost"),
    };
    const original = sumBy(departments, "original_amount");
    totals.discount = original ? Math.max(0, original - totals.sales) : 0;
    totals.guestSales = Math.max(0, totals.sales - totals.memberSales);
    totals.guestOrders = Math.max(0, totals.orders - totals.memberOrders);

    const recent = daily.slice(-7);
    const previous = daily.slice(-14, -7);
    const recentSales = sumBy(recent, "sales_amount");
    const previousSales = sumBy(previous, "sales_amount");

    const weekdayBuckets = WEEKDAY_KEYS.map((key, index) => ({
      key,
      label: $t(key),
      weekend: index === 0 || index === 6,
      total: 0,
      days: 0,
    }));
    daily.forEach((row) => {
      const index = dayjs(row.biz_date).day();
      weekdayBuckets[index].total += toNumber(row.sales_amount);
      weekdayBuckets[index].days += 1;
    });
    const weekdayRows = weekdayBuckets.map((bucket) => ({
      label: bucket.label,
      weekend: bucket.weekend,
      value: bucket.days ? bucket.total / bucket.days : 0,
    }));

    const suppliers = [...suppliersRaw].sort(
      (a, b) => toNumber(b.net_amount) - toNumber(a.net_amount),
    );
    const supplierTotal = sumBy(suppliers, "net_amount");
    const top5Share = ratio(sumBy(suppliers.slice(0, 5), "net_amount"), supplierTotal);

    const riskCounts = stocks.reduce(
      (acc, row) => {
        acc[row.risk_level] = (acc[row.risk_level] || 0) + 1;
        return acc;
      },
      { stockout: 0, low: 0, overstock: 0, slow: 0, healthy: 0 },
    );
    const riskRows = stocks.filter(
      (row) => row.risk_level === "stockout" || row.risk_level === "low",
    );

    const margin = ratio(totals.grossProfit, totals.salesWithCost);
    const coverage = ratio(totals.salesWithCost, totals.sales);

    return {
      daily,
      departments,
      totals,
      recentSales,
      previousSales,
      recentChange: safeChange(recentSales, previousSales),
      memberBasket: totals.memberOrders ? totals.memberSales / totals.memberOrders : 0,
      weekdayRows,
      suppliers,
      supplierTotal,
      top5Share,
      riskCounts,
      riskRows,
      margin,
      coverage,
      latestDate: daily[daily.length - 1]?.biz_date,
      basket: totals.orders ? totals.sales / totals.orders : 0,
      previousBasket: sumBy(previous, "order_count")
        ? previousSales / sumBy(previous, "order_count")
        : 0,
      discountRate: original ? ratio(totals.discount, original) : 0,
      purchaseOrder: sumBy(suppliers, "order_amount"),
      purchaseReceipt: sumBy(suppliers, "receipt_amount"),
      purchaseReturn: sumBy(suppliers, "return_amount"),
      extrasLoaded: Boolean(data.departments),
      stockLoaded: Boolean(data.stocks),
      supplierLoaded: Boolean(data.suppliers),
    };
  }, [data, $t]);

  useEffect(() => {
    if (anchoredRef.current || !view?.latestDate) return;
    if (view.latestDate === endDate) return;
    anchoredRef.current = true;
    const anchor = dayjs(view.latestDate);
    setRange([anchor.subtract(Math.max(days, 1) - 1, "day"), anchor]);
  }, [view, endDate, days]);

  const trendOption = useMemo(
    () => (view ? createTrendOption(view.daily, $t, token) : null),
    [view, $t, token],
  );
  const deptOption = useMemo(
    () => (view ? createDepartmentPieOption(view.departments, $t, token) : null),
    [view, $t, token],
  );
  const marginOption = useMemo(
    () => (view ? createMarginOption(view.departments, $t, token) : null),
    [view, $t, token],
  );
  const weekdayOption = useMemo(
    () => (view ? createWeekdayOption(view.weekdayRows, $t, token) : null),
    [view, $t, token],
  );
  const supplierOption = useMemo(
    () => (view ? createSupplierOption(view.suppliers, $t, token) : null),
    [view, $t, token],
  );

  const riskColumns = [
    {
      title: $t("owner.stock.item"),
      dataIndex: "item_name",
      ellipsis: true,
    },
    {
      title: $t("owner.stock.stock"),
      dataIndex: "stock_qty",
      align: "right",
      render: (value) => formatInteger(value),
    },
    {
      title: $t("owner.stock.sold"),
      dataIndex: "sold_qty",
      align: "right",
      render: (value) => formatInteger(value),
    },
    {
      title: $t("owner.stock.cover"),
      dataIndex: "days_cover",
      align: "right",
      render: (value, row) =>
        row.risk_level === "stockout" ? $t("owner.stock.out") : `${toNumber(value).toFixed(1)}`,
    },
    {
      title: $t("owner.stock.status"),
      dataIndex: "risk_level",
      render: (value) => (
        <Tag color={value === "stockout" ? "red" : value === "low" ? "orange" : "default"}>
          {value === "stockout" ? $t("owner.stock.out") : $t("owner.stock.low")}
        </Tag>
      ),
    },
  ];

  const insights = useMemo(() => {
    if (!view) return [];
    const list = [];
    if (view.recentChange !== null) {
      list.push({
        key: "trend",
        color: view.recentChange >= 0 ? "green" : "red",
        text: $t("owner.insight.trend", {
          change: `${view.recentChange >= 0 ? "+" : ""}${view.recentChange.toFixed(1)}`,
          recent: formatWan(view.recentSales),
          previous: formatWan(view.previousSales),
        }),
      });
    }
    const weekendAvg =
      view.weekdayRows.filter((row) => row.weekend).reduce((t, row) => t + row.value, 0) / 2;
    const workdayAvg =
      view.weekdayRows.filter((row) => !row.weekend).reduce((t, row) => t + row.value, 0) / 5;
    if (workdayAvg > 0) {
      list.push({
        key: "weekday",
        color: "blue",
        text: $t("owner.insight.weekend", {
          weekend: formatWan(weekendAvg),
          workday: formatWan(workdayAvg),
          lift: formatPct(ratio(weekendAvg - workdayAvg, workdayAvg), 0),
        }),
      });
    }
    list.push({
      key: "margin",
      color: view.margin >= 15 ? "green" : "orange",
      text: $t("owner.insight.margin", {
        margin: formatPct(view.margin),
        coverage: formatPct(view.coverage),
      }),
    });
    if (view.supplierLoaded && view.top5Share > 0) {
      list.push({
        key: "supplier",
        color: view.top5Share > 60 ? "orange" : "default",
        text: $t("owner.insight.supplier", {
          share: formatPct(view.top5Share),
          count: formatInteger(view.suppliers.length),
        }),
      });
    }
    const riskTotal = view.riskCounts.stockout + view.riskCounts.low;
    if (view.stockLoaded && riskTotal > 0) {
      list.push({
        key: "stock",
        color: "red",
        text: $t("owner.insight.stock", {
          stockout: formatInteger(view.riskCounts.stockout),
          low: formatInteger(view.riskCounts.low),
        }),
      });
    }
    return list;
  }, [view, $t]);

  return (
    <main className="cockpit-container">
      <header className="cockpit-header">
        <div className="cockpit-header__main">
          <h1 className="cockpit-header__title">{$t("owner.title")}</h1>
          <p className="cockpit-header__subtitle">
            {$t("owner.subtitle", {
              start: startDate,
              end: endDate,
              latest: view?.latestDate || "-",
            })}
          </p>
        </div>
        <div className="cockpit-header__actions">
          <Segmented
            options={[
              { label: $t("owner.range.7"), value: 7 },
              { label: $t("owner.range.30"), value: 30 },
            ]}
            value={days}
            onChange={(value) => {
              const anchor = view?.latestDate ? dayjs(view.latestDate) : dayjs();
              setDays(value);
              setRange([anchor.subtract(value - 1, "day").startOf("day"), anchor.startOf("day")]);
            }}
          />
          <DatePicker.RangePicker
            allowClear={false}
            value={range}
            onChange={(dates) => {
              if (dates && dates[0] && dates[1]) {
                setRange([dates[0].startOf("day"), dates[1].startOf("day")]);
                setDays(0);
              }
            }}
          />
          <Button loading={loading} onClick={() => void load(true)}>
            {$t("owner.action.reload")}
          </Button>
        </div>
      </header>
      <p className="cockpit-header__hint">{$t("owner.hint")}</p>

      {error ? (
        <Alert
          action={
            <Button size="small" onClick={() => void load(true)}>
              {$t("owner.action.retry")}
            </Button>
          }
          message={$t("owner.error.title")}
          description={$t("owner.error.desc")}
          showIcon
          type="error"
        />
      ) : null}

      {!view ? (
        loading ? (
          <div className="cockpit-loading">
            <Skeleton active paragraph={{ rows: 10 }} />
          </div>
        ) : error ? null : (
          <Empty description={$t("owner.empty")} />
        )
      ) : (
        <>
          <section className="cockpit-insights">
            {insights.map((insight) => (
              <Tag key={insight.key} className="cockpit-insight" color={insight.color}>
                {insight.text}
              </Tag>
            ))}
          </section>

          <section className="cockpit-kpis">
            <Card className="cockpit-kpi" classNames={{ body: "cockpit-kpi__body" }}>
              <Statistic
                title={$t("owner.kpi.sales")}
                value={toNumber(view.totals.sales)}
                precision={0}
                prefix="¥"
              />
              <span className="cockpit-kpi__hint">
                {$t("owner.kpi.salesHint", {
                  recent: formatWan(view.recentSales),
                  change:
                    view.recentChange === null
                      ? "-"
                      : `${view.recentChange >= 0 ? "+" : ""}${view.recentChange.toFixed(1)}%`,
                })}
              </span>
            </Card>
            <Card className="cockpit-kpi" classNames={{ body: "cockpit-kpi__body" }}>
              <Statistic
                title={$t("owner.kpi.dailyAvg")}
                value={toNumber(view.totals.sales / view.daily.length)}
                precision={0}
                prefix="¥"
              />
              <span className="cockpit-kpi__hint">
                {$t("owner.kpi.dailyHint", { days: formatInteger(view.daily.length) })}
              </span>
            </Card>
            <Card className="cockpit-kpi" classNames={{ body: "cockpit-kpi__body" }}>
              <Statistic
                title={$t("owner.kpi.basket")}
                value={toNumber(view.basket)}
                precision={2}
                prefix="¥"
              />
              <span className="cockpit-kpi__hint">
                {$t("owner.kpi.basketHint", {
                  orders: formatInteger(view.totals.orders),
                  change:
                    view.previousBasket > 0
                      ? formatPct(ratio(view.basket - view.previousBasket, view.previousBasket))
                      : "-",
                })}
              </span>
            </Card>
            <Card className="cockpit-kpi" classNames={{ body: "cockpit-kpi__body" }}>
              <Statistic
                title={$t("owner.kpi.margin")}
                value={toNumber(view.margin)}
                precision={1}
                suffix="%"
              />
              <span className="cockpit-kpi__hint">
                {$t("owner.kpi.marginHint", {
                  profit: formatWan(view.totals.grossProfit),
                  coverage: formatPct(view.coverage),
                })}
              </span>
            </Card>
            <Card className="cockpit-kpi" classNames={{ body: "cockpit-kpi__body" }}>
              <Statistic
                title={$t("owner.kpi.member")}
                value={ratio(view.totals.memberSales, view.totals.sales)}
                precision={1}
                suffix="%"
              />
              <span className="cockpit-kpi__hint">
                {$t("owner.kpi.memberHint", {
                  orders: formatInteger(view.totals.memberOrders),
                })}
              </span>
            </Card>
            <Card className="cockpit-kpi" classNames={{ body: "cockpit-kpi__body" }}>
              <Statistic
                title={$t("owner.kpi.purchase")}
                value={toNumber(view.purchaseReceipt)}
                precision={0}
                prefix="¥"
              />
              <span className="cockpit-kpi__hint">
                {$t("owner.kpi.purchaseHint", {
                  order: formatWan(view.purchaseOrder),
                  ret: formatWan(view.purchaseReturn),
                })}
              </span>
            </Card>
          </section>

          <section className="cockpit-grid">
            <Card className="cockpit-card cockpit-span-2" title={$t("owner.chart.trendTitle")}>
              <p className="cockpit-card__desc">
                {$t("owner.chart.trendDesc", {
                  discount: formatPct(view.discountRate),
                })}
              </p>
              <Chart className="cockpit-chart--main" option={trendOption} />
            </Card>

            <Card className="cockpit-card" title={$t("owner.chart.deptTitle")}>
              <p className="cockpit-card__desc">{$t("owner.chart.deptDesc")}</p>
              {view.extrasLoaded ? (
                <Chart className="cockpit-chart--normal" option={deptOption} />
              ) : (
                <Skeleton active paragraph={{ rows: 5 }} />
              )}
            </Card>

            <Card className="cockpit-card" title={$t("owner.chart.marginTitle")}>
              <p className="cockpit-card__desc">{$t("owner.chart.marginDesc")}</p>
              {view.extrasLoaded ? (
                <Chart className="cockpit-chart--normal" option={marginOption} />
              ) : (
                <Skeleton active paragraph={{ rows: 5 }} />
              )}
            </Card>

            <Card className="cockpit-card" title={$t("owner.chart.weekdayTitle")}>
              <p className="cockpit-card__desc">{$t("owner.chart.weekdayDesc")}</p>
              <Chart className="cockpit-chart--normal" option={weekdayOption} />
            </Card>

            <Card className="cockpit-card" title={$t("owner.chart.memberTitle")}>
              <div className="cockpit-member">
                <div>
                  <span className="cockpit-member__label">{$t("owner.member.member")}</span>
                  <strong className="cockpit-member__value">
                    {formatWan(view.totals.memberSales)}
                  </strong>
                  <span className="cockpit-member__hint">
                    {$t("owner.member.orders", {
                      count: formatInteger(view.totals.memberOrders),
                    })}
                  </span>
                </div>
                <div>
                  <span className="cockpit-member__label">{$t("owner.member.guest")}</span>
                  <strong className="cockpit-member__value">
                    {formatWan(view.totals.guestSales)}
                  </strong>
                  <span className="cockpit-member__hint">
                    {$t("owner.member.orders", {
                      count: formatInteger(view.totals.guestOrders),
                    })}
                  </span>
                </div>
              </div>
              <div className="cockpit-facts">
                <div className="cockpit-fact">
                  <span>{$t("owner.fact.memberBasket")}</span>
                  <strong>{formatMoney(view.memberBasket)}</strong>
                  <em>{$t("owner.fact.memberBasketHint")}</em>
                </div>
                <div className="cockpit-fact">
                  <span>{$t("owner.fact.discount")}</span>
                  <strong>{view.extrasLoaded ? formatPct(view.discountRate) : "-"}</strong>
                  <em>
                    {view.extrasLoaded
                      ? $t("owner.fact.discountHint", { amount: formatWan(view.totals.discount) })
                      : $t("owner.common.loading")}
                  </em>
                </div>
                <div className="cockpit-fact">
                  <span>{$t("owner.fact.refund")}</span>
                  <strong>{formatWan(view.totals.refund)}</strong>
                  <em>{$t("owner.fact.refundHint")}</em>
                </div>
              </div>
            </Card>

            <Card className="cockpit-card cockpit-span-2" title={$t("owner.chart.supplierTitle")}>
              <p className="cockpit-card__desc">
                {$t("owner.chart.supplierDesc", {
                  count: formatInteger(view.suppliers.length),
                  top5: formatPct(view.top5Share),
                })}
              </p>
              {view.supplierLoaded ? (
                <Chart className="cockpit-chart--normal" option={supplierOption} />
              ) : (
                <Skeleton active paragraph={{ rows: 5 }} />
              )}
            </Card>

            <Card className="cockpit-card cockpit-span-2" title={$t("owner.stock.title")}>
              <p className="cockpit-card__desc">{$t("owner.stock.desc")}</p>
              {view.stockLoaded ? (
                <div className="cockpit-stock">
                <div className="cockpit-stock__stats">
                  <div className="cockpit-stat">
                    <span>{$t("owner.stock.outCount")}</span>
                    <strong>{formatInteger(view.riskCounts.stockout)}</strong>
                  </div>
                  <div className="cockpit-stat">
                    <span>{$t("owner.stock.lowCount")}</span>
                    <strong>{formatInteger(view.riskCounts.low)}</strong>
                  </div>
                  <div className="cockpit-stat">
                    <span>{$t("owner.stock.overCount")}</span>
                    <strong>{formatInteger(view.riskCounts.overstock)}</strong>
                  </div>
                  <div className="cockpit-stat">
                    <span>{$t("owner.stock.slowCount")}</span>
                    <strong>{formatInteger(view.riskCounts.slow)}</strong>
                  </div>
                  <div className="cockpit-stat">
                    <span>{$t("owner.stock.healthyCount")}</span>
                    <strong>{formatInteger(view.riskCounts.healthy)}</strong>
                  </div>
                </div>
                <Table
                  columns={riskColumns}
                  dataSource={view.riskRows.slice(0, 8)}
                  pagination={false}
                  rowKey={(row) => `${row.branch_id}-${row.item_id}`}
                  size="small"
                />
                </div>
              ) : (
                <Skeleton active paragraph={{ rows: 5 }} />
              )}
            </Card>
          </section>

          <footer className="cockpit-footer">
            <h3 className="cockpit-footer__title">{$t("owner.footer.title")}</h3>
            <p>{$t("owner.footer.scope", { orders: formatInteger(view.totals.orders) })}</p>
            <p>{$t("owner.footer.margin")}</p>
            <p>{$t("owner.footer.member")}</p>
            <p>{$t("owner.footer.stock")}</p>
            <p>{$t("owner.footer.generated", { time: dayjs().format("YYYY-MM-DD HH:mm") })}</p>
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
      <OwnerCockpit />
    </ConfigProvider>
  );
};

export default App;
