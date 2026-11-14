import React, { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  Alert,
  Button,
  ConfigProvider,
  DatePicker,
  Empty,
  Input,
  Segmented,
  Select,
  Skeleton,
  Table,
  Tag,
  Tooltip,
  theme as antdTheme,
} from "antd";
import { ReloadOutlined, InfoCircleOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import { useAppTheme, useI18n, useSdkClient } from "@/context/app-context";
import Chart from "../components/Chart";
import CoverageStrip from "../components/CoverageStrip";
import MetricDrawer from "../components/MetricDrawer";
import MetricTable from "../components/MetricTable";
import { locales } from "../locales";
import { formatCurrency, formatNumber, formatPercent } from "../lib/format";
import "./index.css";

const { RangePicker } = DatePicker;
const ENDPOINT = "businessMetricGateway";
const DEFAULT_DIMENSION = {
  retail_performance: "branch",
  inventory_health: "branch",
  purchase_performance: "day",
  marketing_performance: "promotion",
};
const getThemeConfig = (appTheme) => ({
  cssVar: { key: "metric-center-theme" },
  algorithm: appTheme?.mode === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
  ...(appTheme?.skinColor ? { token: { colorPrimary: appTheme.skinColor } } : {}),
});

const axisStyle = (token) => ({
  axisLine: { lineStyle: { color: token.colorBorderSecondary } },
  axisTick: { show: false },
  axisLabel: { color: token.colorTextSecondary, fontSize: 11 },
  splitLine: { lineStyle: { color: token.colorBorderSecondary, type: "dashed" } },
  nameTextStyle: { color: token.colorTextSecondary },
});

const SectionHeading = ({ title, desc, action }) => (
  <div className="metric-section__heading">
    <div>
      <h2>{title}</h2>
      {desc ? <p>{desc}</p> : null}
    </div>
    {action || null}
  </div>
);

const FactStrip = ({ items }) => (
  <div className="metric-facts">
    {items.map((item) => (
      <div className="metric-fact" key={item.label}>
        <span className="metric-fact__label">{item.label}</span>
        <strong className={item.tone ? `metric-fact__value is-${item.tone}` : "metric-fact__value"}>
          {item.value}
        </strong>
        {item.hint ? <span className="metric-fact__hint">{item.hint}</span> : null}
      </div>
    ))}
  </div>
);

const LoadingBlock = ({ text, height = 290 }) => (
  <div className="metric-loading" style={{ minHeight: height }}>
    <Skeleton active title={false} paragraph={{ rows: 5 }} />
    <span className="metric-loading__text">{text}</span>
  </div>
);

const ErrorBlock = ({ $t, onRetry }) => (
  <div className="metric-error-block">
    <Alert
      type="error"
      showIcon
      message={$t("common.errorTitle")}
      description={$t("common.errorDesc")}
      action={
        <Button size="small" onClick={onRetry}>
          {$t("common.retry")}
        </Button>
      }
    />
  </div>
);

const EmptyBlock = ({ text }) => (
  <div className="metric-empty">
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={text} />
  </div>
);

const AppContent = () => {
  const client = useSdkClient();
  const $i18n = useI18n();
  const appTheme = useAppTheme();
  const { token } = antdTheme.useToken();
  const [dataVersion, bump] = useReducer((value) => value + 1, 0);

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

  const [catalog, setCatalog] = useState(null);
  const [initError, setInitError] = useState(false);
  const [dates, setDates] = useState(null);
  const [rangeDays, setRangeDays] = useState(7);
  const [latestDate, setLatestDate] = useState(null);
  const [branchId, setBranchId] = useState(undefined);
  const [branchOptions, setBranchOptions] = useState([]);
  const [coverage, setCoverage] = useState(null);
  const [warnings, setWarnings] = useState([]);
  const [activeTab, setActiveTab] = useState("overview");
  const [loading, setLoading] = useState({});
  const [errors, setErrors] = useState({});
  const [selectedMetric, setSelectedMetric] = useState(null);
  const [dictGroup, setDictGroup] = useState("all");
  const [dictQuery, setDictQuery] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const [initToken, setInitToken] = useState(0);

  const cacheRef = useRef({});
  const inflightRef = useRef({});

  const callGateway = useCallback(
    (params) =>
      client.bff.execute({
        scriptName: ENDPOINT,
        params,
      }),
    [client],
  );

  useEffect(() => {
    let alive = true;
    const initialize = async () => {
      const results = await Promise.allSettled([
        callGateway({ action: "catalog" }),
        callGateway({ action: "health" }),
      ]);
      if (!alive) return;
      const [catalogResult, healthResult] = results;
      if (catalogResult.status === "fulfilled") setCatalog(catalogResult.value);
      else setInitError(true);
      let end = dayjs();
      if (healthResult.status === "fulfilled") {
        const health = healthResult.value;
        setCoverage(health.coverage || null);
        setWarnings(health.warnings || []);
        if (health.coverage?.latest_biz_date) end = dayjs(health.coverage.latest_biz_date);
      } else {
        setInitError(true);
      }
      setLatestDate(end.format("YYYY-MM-DD"));
      setDates([end.subtract(6, "day"), end]);
    };
    initialize();
    return () => {
      alive = false;
    };
  }, [callGateway, initToken]);

  const startDate = dates?.[0]?.format("YYYY-MM-DD") || "";
  const endDate = dates?.[1]?.format("YYYY-MM-DD") || "";
  const scopeKey = useMemo(
    () => `${startDate}|${endDate}|${branchId || "all"}`,
    [startDate, endDate, branchId],
  );

  const load = useCallback(
    async (group, dimension) => {
      if (!startDate || !endDate) return null;
      const key = `${group}|${dimension}|${scopeKey}`;
      const viewKey = `${group}:${dimension}`;
      if (cacheRef.current[key]) return cacheRef.current[key];
      if (inflightRef.current[key]) return inflightRef.current[key];
      setLoading((previous) => ({ ...previous, [viewKey]: true }));
      setErrors((previous) => {
        const next = { ...previous };
        delete next[viewKey];
        return next;
      });
      const task = callGateway({
        action: "query",
        groupCode: group,
        startDate,
        endDate,
        dimension,
        ...(branchId ? { branchId } : {}),
      })
        .then((result) => {
          cacheRef.current[key] = result;
          setCoverage(result.coverage || null);
          setWarnings(result.warnings || []);
          if (group === "retail_performance" && dimension === "branch") {
            const options = (result.rows || [])
              .filter((row) => row.branch_id)
              .map((row) => ({
                value: row.branch_id,
                label: row.branch_name || row.branch_id,
              }));
            setBranchOptions(options);
          }
          bump();
          return result;
        })
        .catch((error) => {
          setErrors((previous) => ({
            ...previous,
            [viewKey]: error?.message || "ERROR",
          }));
          return null;
        })
        .finally(() => {
          delete inflightRef.current[key];
          setLoading((previous) => ({ ...previous, [viewKey]: false }));
        });
      inflightRef.current[key] = task;
      return task;
    },
    [branchId, callGateway, endDate, scopeKey, startDate],
  );

  const getResult = useCallback(
    (group, dimension = DEFAULT_DIMENSION[group]) => {
      if (!startDate || !endDate) return null;
      return cacheRef.current[`${group}|${dimension}|${scopeKey}`] || null;
    },
    [endDate, scopeKey, startDate],
  );

  const ensureView = useCallback(
    (tab) => {
      if (!startDate || !endDate) return;
      if (tab === "overview") {
        load("retail_performance", "branch").then(() => {
          load("inventory_health", "branch");
          load("purchase_performance", "day");
          load("marketing_performance", "promotion");
          load("retail_performance", "day");
          load("retail_performance", "category");
        });
      } else if (tab === "sales") {
        load("retail_performance", "day");
        load("retail_performance", "category");
      } else if (tab === "inventory") {
        load("inventory_health", "branch");
      } else if (tab === "purchase") {
        load("purchase_performance", "day");
        load("purchase_performance", "supplier");
      } else if (tab === "marketing") {
        load("marketing_performance", "promotion");
      }
    },
    [endDate, load, startDate],
  );

  useEffect(() => {
    if (dates?.length === 2) ensureView(activeTab);
  }, [activeTab, dates, ensureView, refreshToken]);

  const applyPreset = (days) => {
    const end = dayjs(latestDate || undefined);
    setRangeDays(days);
    setDates([end.subtract(days - 1, "day"), end]);
  };

  const handleRefresh = () => {
    cacheRef.current = {};
    inflightRef.current = {};
    setErrors({});
    setRefreshToken((value) => value + 1);
  };

  const openMetricByCode = useCallback(
    (code) => {
      const metric = catalog?.metrics?.find((item) => item.code === code);
      if (metric) setSelectedMetric(metric);
    },
    [catalog],
  );

  const groupMap = useMemo(
    () => new Map((catalog?.groups || []).map((group) => [group.code, group])),
    [catalog],
  );

  const valuesByMetric = useMemo(() => {
    const map = {};
    Object.entries(DEFAULT_DIMENSION).forEach(([group, dimension]) => {
      const result = getResult(group, dimension);
      if (result?.metrics) Object.assign(map, result.metrics);
    });
    return map;
  }, [getResult, coverage, warnings, refreshToken, dataVersion]);

  const selectedValue = selectedMetric ? valuesByMetric[selectedMetric.code] : undefined;

  const moneyAxis = useMemo(
    () => ({
      type: "value",
      name: $t("chart.money"),
      ...axisStyle(token),
      axisLabel: { ...axisStyle(token).axisLabel, formatter: (value) => formatCurrency(value, { digits: 0 }) },
    }),
    [$t, token],
  );

  const baseChart = useMemo(
    () => ({
      color: [token.colorPrimary, token.colorWarning, token.colorError, token.colorSuccess, token.colorInfo],
      textStyle: { color: token.colorTextSecondary },
      tooltip: {
        trigger: "axis",
        backgroundColor: token.colorBgElevated,
        borderColor: token.colorBorderSecondary,
        textStyle: { color: token.colorText },
      },
      grid: { top: 34, right: 18, bottom: 20, left: 18, containLabel: true },
      legend: {
        top: 0,
        right: 0,
        icon: "roundRect",
        itemWidth: 8,
        itemHeight: 8,
        textStyle: { color: token.colorTextSecondary, fontSize: 11 },
      },
    }),
    [token],
  );

  const overview = {
    retail: getResult("retail_performance", "branch"),
    trend: getResult("retail_performance", "day"),
    inventory: getResult("inventory_health", "branch"),
    purchase: getResult("purchase_performance", "day"),
    marketing: getResult("marketing_performance", "promotion"),
  };
  const retailMetrics = overview.retail?.metrics || {};
  const inventoryMetrics = overview.inventory?.metrics || {};
  const purchaseMetrics = overview.purchase?.metrics || {};
  const marketingMetrics = overview.marketing?.metrics || {};

  const sparkOption = useMemo(() => {
    const rows = overview.trend?.rows || [];
    if (!rows.length) return null;
    return {
      ...baseChart,
      grid: { top: 8, right: 4, bottom: 4, left: 4 },
      legend: { show: false },
      tooltip: {
        trigger: "axis",
        backgroundColor: token.colorBgElevated,
        borderColor: token.colorBorderSecondary,
        textStyle: { color: token.colorText },
        formatter: (items) => `${items[0]?.axisValue || ""}<br/>${$t("chart.netSales")} ${formatCurrency(items[0]?.value)}`,
      },
      xAxis: { type: "category", show: false, data: rows.map((row) => row.biz_date) },
      yAxis: { type: "value", show: false },
      series: [
        {
          type: "line",
          smooth: 0.25,
          symbol: "none",
          data: rows.map((row) => Number(row.net_sales_amount || 0)),
          lineStyle: { width: 2 },
          areaStyle: { opacity: 0.09 },
        },
      ],
    };
  }, [baseChart, overview.trend, $t, token, dataVersion]);

  const salesTrendOption = useMemo(() => {
    const rows = getResult("retail_performance", "day")?.rows || [];
    if (!rows.length) return null;
    return {
      ...baseChart,
      xAxis: { type: "category", data: rows.map((row) => row.biz_date), ...axisStyle(token), splitLine: { show: false } },
      yAxis: moneyAxis,
      series: [
        {
          name: $t("chart.netSales"),
          type: "line",
          smooth: 0.22,
          symbol: "circle",
          symbolSize: 5,
          data: rows.map((row) => Number(row.net_sales_amount || 0)),
          areaStyle: { opacity: 0.08 },
        },
        {
          name: $t("chart.refund"),
          type: "bar",
          barMaxWidth: 12,
          data: rows.map((row) => Number(row.refund_amount || 0)),
        },
      ],
    };
  }, [baseChart, getResult, moneyAxis, token, $t, dataVersion]);

  const categoryOption = useMemo(() => {
    const rows = [...(getResult("retail_performance", "category")?.rows || [])]
      .sort((a, b) => Number(b.net_sales_amount || 0) - Number(a.net_sales_amount || 0))
      .slice(0, 10)
      .reverse();
    if (!rows.length) return null;
    return {
      ...baseChart,
      legend: { show: false },
      grid: { top: 4, right: 24, bottom: 8, left: 8, containLabel: true },
      xAxis: { type: "value", ...axisStyle(token), axisLabel: { ...axisStyle(token).axisLabel, formatter: (value) => formatCurrency(value) } },
      yAxis: { type: "category", data: rows.map((row) => row.dim_label || row.dim_key), ...axisStyle(token), splitLine: { show: false } },
      series: [{ type: "bar", barMaxWidth: 16, data: rows.map((row) => Number(row.net_sales_amount || 0)) }],
    };
  }, [baseChart, getResult, token, dataVersion]);

  const inventoryRiskOption = useMemo(() => {
    const metrics = getResult("inventory_health", "branch")?.metrics || {};
    const data = [
      { name: $t("inventory.risk.stockout"), value: Number(metrics.stockout_sku_count || 0) },
      { name: $t("inventory.risk.low"), value: Number(metrics.low_stock_sku_count || 0) },
      { name: $t("inventory.risk.overstock"), value: Number(metrics.overstock_sku_count || 0) },
      { name: $t("inventory.risk.slow"), value: Number(metrics.slow_sku_count || 0) },
    ];
    if (!data.some((item) => item.value > 0)) return null;
    return {
      ...baseChart,
      tooltip: { ...baseChart.tooltip, trigger: "item" },
      legend: { bottom: 0, left: "center", textStyle: { color: token.colorTextSecondary, fontSize: 11 } },
      series: [
        {
          type: "pie",
          radius: ["54%", "76%"],
          center: ["50%", "43%"],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: token.colorBgContainer, borderWidth: 2 },
          label: { color: token.colorTextSecondary, formatter: "{b}\n{c}" },
          data,
        },
      ],
    };
  }, [baseChart, getResult, $t, token, dataVersion]);

  const purchaseTrendOption = useMemo(() => {
    const rows = getResult("purchase_performance", "day")?.rows || [];
    if (!rows.length) return null;
    return {
      ...baseChart,
      xAxis: { type: "category", data: rows.map((row) => row.biz_date), ...axisStyle(token), splitLine: { show: false } },
      yAxis: moneyAxis,
      series: [
        {
          name: $t("chart.orderAmount"),
          type: "bar",
          barMaxWidth: 16,
          data: rows.map((row) => Number(row.purchase_order_amount || 0)),
        },
        {
          name: $t("chart.netReceipt"),
          type: "line",
          smooth: 0.2,
          symbol: "circle",
          symbolSize: 5,
          data: rows.map((row) => Number(row.net_receipt_amount || 0)),
        },
      ],
    };
  }, [baseChart, getResult, moneyAxis, token, $t, dataVersion]);

  const supplierOption = useMemo(() => {
    const rows = [...(getResult("purchase_performance", "supplier")?.rows || [])]
      .sort((a, b) => Number(b.net_receipt_amount || 0) - Number(a.net_receipt_amount || 0))
      .slice(0, 8)
      .reverse();
    if (!rows.length) return null;
    return {
      ...baseChart,
      legend: { show: false },
      grid: { top: 4, right: 28, bottom: 8, left: 8, containLabel: true },
      xAxis: { type: "value", ...axisStyle(token), axisLabel: { ...axisStyle(token).axisLabel, formatter: (value) => formatCurrency(value) } },
      yAxis: {
        type: "category",
        data: rows.map((row) => row.dim_label || row.supplier_name || row.dim_key),
        ...axisStyle(token),
        splitLine: { show: false },
      },
      series: [{ type: "bar", barMaxWidth: 14, data: rows.map((row) => Number(row.net_receipt_amount || 0)) }],
    };
  }, [baseChart, getResult, token, dataVersion]);

  const marketingOption = useMemo(() => {
    const rows = [...(getResult("marketing_performance", "promotion")?.rows || [])]
      .sort((a, b) => Number(b.marketing_discount_amount || 0) - Number(a.marketing_discount_amount || 0))
      .slice(0, 10)
      .reverse();
    if (!rows.length) return null;
    return {
      ...baseChart,
      legend: { show: false },
      grid: { top: 4, right: 24, bottom: 8, left: 8, containLabel: true },
      xAxis: { type: "value", ...axisStyle(token), axisLabel: { ...axisStyle(token).axisLabel, formatter: (value) => formatCurrency(value) } },
      yAxis: {
        type: "category",
        data: rows.map((row) => row.dim_label || row.dim_key),
        ...axisStyle(token),
        splitLine: { show: false },
      },
      series: [{ type: "bar", barMaxWidth: 16, data: rows.map((row) => Number(row.marketing_discount_amount || 0)) }],
    };
  }, [baseChart, getResult, token, dataVersion]);

  const inventoryRows = useMemo(
    () =>
      [...(getResult("inventory_health", "branch")?.rows || [])]
        .filter((row) => row.days_cover_7d !== null && row.days_cover_7d !== undefined)
        .sort((a, b) => Number(a.days_cover_7d) - Number(b.days_cover_7d))
        .slice(0, 8),
    [getResult, coverage, warnings, dataVersion],
  );

  const supplierRows = useMemo(
    () =>
      [...(getResult("purchase_performance", "supplier")?.rows || [])]
        .sort((a, b) => Number(b.net_receipt_amount || 0) - Number(a.net_receipt_amount || 0))
        .slice(0, 8),
    [getResult, coverage, warnings, dataVersion],
  );

  const marketingRows = useMemo(
    () =>
      [...(getResult("marketing_performance", "promotion")?.rows || [])]
        .sort((a, b) => Number(b.marketing_discount_amount || 0) - Number(a.marketing_discount_amount || 0))
        .slice(0, 10),
    [getResult, coverage, warnings, dataVersion],
  );

  const renderOverview = () => {
    const key = "retail_performance:branch";
    if (loading[key] && !overview.retail) return <LoadingBlock text={$t("common.loadingData")} height={420} />;
    if (errors[key] && !overview.retail) return <ErrorBlock $t={$t} onRetry={() => ensureView("overview")} />;
    if (!overview.retail) return <EmptyBlock text={$t("common.empty")} />;
    const riskTotal =
      Number(inventoryMetrics.stockout_sku_count || 0) +
      Number(inventoryMetrics.low_stock_sku_count || 0) +
      Number(inventoryMetrics.overstock_sku_count || 0) +
      Number(inventoryMetrics.slow_sku_count || 0);

    return (
      <div className="metric-overview">
        <section className="metric-hero">
          <div className="metric-hero__main">
            <span className="metric-hero__label">{$t("overview.netSales")}</span>
            <strong className="metric-hero__value">{formatCurrency(retailMetrics.net_sales_amount)}</strong>
            <div className="metric-hero__meta">
              <span>
                {$t("overview.refund")} <b>{formatCurrency(retailMetrics.refund_amount)}</b>
              </span>
              <span>
                {$t("overview.costCoverage")} <b>{formatPercent(retailMetrics.cost_coverage_rate)}</b>
              </span>
            </div>
            <div className="metric-hero__chart">
              {sparkOption ? (
                <Chart option={sparkOption} height={104} ariaLabel={$t("overview.trendTitle")} />
              ) : (
                <Skeleton active title={false} paragraph={{ rows: 2 }} />
              )}
            </div>
          </div>
          <div className="metric-hero__side">
            <FactStrip
              items={[
                {
                  label: $t("overview.aov"),
                  value: formatCurrency(retailMetrics.avg_order_value),
                  hint: $t("overview.aovHint"),
                },
                {
                  label: $t("overview.orders"),
                  value: formatNumber(retailMetrics.order_count, 0),
                  hint: $t("overview.ordersHint"),
                },
                {
                  label: $t("overview.memberShare"),
                  value: formatPercent(retailMetrics.member_sales_share),
                  hint: $t("overview.memberShareHint"),
                },
                {
                  label: $t("overview.margin"),
                  value: formatPercent(retailMetrics.estimated_gross_margin),
                  hint: $t("overview.marginHint"),
                },
              ]}
            />
          </div>
        </section>

        <p className="metric-insight">
          {$t("overview.insight", {
            net: formatCurrency(retailMetrics.net_sales_amount),
            coverage: formatPercent(retailMetrics.cost_coverage_rate),
          })}
        </p>

        <section className="metric-section">
          <SectionHeading
            title={$t("overview.pulse")}
            desc={$t("overview.pulseHint")}
            action={
              <Button type="text" size="small" icon={<InfoCircleOutlined />} onClick={() => setActiveTab("dictionary")}>
                {$t("page.action.definition")}
              </Button>
            }
          />
          <div className="metric-pulse">
            {[
              {
                key: "inventory",
                title: $t("overview.inventoryPulse"),
                value: formatNumber(riskTotal, 0),
                unit: "SKU",
                hint: $t("overview.inventoryPulseHint"),
                loaded: Boolean(overview.inventory),
                tab: "inventory",
              },
              {
                key: "purchase",
                title: $t("overview.purchasePulse"),
                value: formatCurrency(purchaseMetrics.net_receipt_amount),
                unit: "",
                hint: $t("overview.purchasePulseHint", {
                  share: formatPercent(purchaseMetrics.supplier_top5_share),
                }),
                loaded: Boolean(overview.purchase),
                tab: "purchase",
              },
              {
                key: "marketing",
                title: $t("overview.marketingPulse"),
                value: formatCurrency(marketingMetrics.marketing_discount_amount),
                unit: "",
                hint: $t("overview.marketingPulseHint", {
                  link: formatPercent(marketingMetrics.promotion_link_rate),
                }),
                loaded: Boolean(overview.marketing),
                tab: "marketing",
              },
            ].map((item) => (
              <button
                key={item.key}
                type="button"
                className="metric-pulse__item"
                onClick={() => setActiveTab(item.tab)}
              >
                <span className="metric-pulse__label">{item.title}</span>
                {item.loaded ? (
                  <strong className="metric-pulse__value">
                    {item.value}
                    {item.unit ? <small>{item.unit}</small> : null}
                  </strong>
                ) : (
                  <span className="metric-pulse__loading">{$t("overview.loadingPulse")}</span>
                )}
                <span className="metric-pulse__hint">{item.hint}</span>
                <span className="metric-pulse__link">{$t("overview.enter")} →</span>
              </button>
            ))}
          </div>
        </section>

        <section className="metric-section">
          <SectionHeading title={$t("overview.trendTitle")} desc={$t("overview.trendDesc")} />
          {salesTrendOption ? (
            <Chart option={salesTrendOption} height={320} ariaLabel={$t("overview.trendTitle")} />
          ) : (
            <LoadingBlock text={$t("common.loadingData")} />
          )}
        </section>
      </div>
    );
  };

  const renderSales = () => {
    const key = "retail_performance:day";
    const dayResult = getResult("retail_performance", "day");
    const metrics = dayResult?.metrics || retailMetrics;
    return (
      <div>
        <SectionHeading title={$t("sales.title")} desc={$t("sales.desc")} />
        {errors[key] && !dayResult ? <ErrorBlock $t={$t} onRetry={() => ensureView("sales")} /> : null}
        {!dayResult && loading[key] ? <LoadingBlock text={$t("common.loadingData")} /> : null}
        {!dayResult && !loading[key] && !errors[key] ? <EmptyBlock text={$t("common.empty")} /> : null}
        {dayResult ? (
          <>
            <FactStrip
              items={[
                { label: $t("overview.netSales"), value: formatCurrency(metrics.net_sales_amount) },
                { label: $t("overview.refund"), value: formatCurrency(metrics.refund_amount) },
                { label: $t("overview.aov"), value: formatCurrency(metrics.avg_order_value) },
                { label: $t("overview.orders"), value: formatNumber(metrics.order_count, 0) },
                { label: $t("overview.memberShare"), value: formatPercent(metrics.member_sales_share) },
                { label: $t("overview.margin"), value: formatPercent(metrics.estimated_gross_margin) },
              ]}
            />
            <div className="metric-chart-grid">
              <section className="metric-section metric-section--flush">
                <SectionHeading title={$t("overview.trendTitle")} desc={$t("overview.trendDesc")} />
                {salesTrendOption ? <Chart option={salesTrendOption} height={330} ariaLabel={$t("overview.trendTitle")} /> : <EmptyBlock text={$t("common.empty")} />}
              </section>
              <section className="metric-section metric-section--flush">
                <SectionHeading title={$t("sales.categoryTitle")} desc={$t("sales.categoryDesc")} />
                {categoryOption ? <Chart option={categoryOption} height={330} ariaLabel={$t("sales.categoryTitle")} /> : <EmptyBlock text={$t("common.empty")} />}
              </section>
            </div>
          </>
        ) : null}
      </div>
    );
  };

  const renderInventory = () => {
    const key = "inventory_health:branch";
    const result = getResult("inventory_health", "branch");
    const metrics = result?.metrics || {};
    return (
      <div>
        <SectionHeading title={$t("inventory.title")} desc={$t("inventory.desc")} />
        {errors[key] && !result ? <ErrorBlock $t={$t} onRetry={() => ensureView("inventory")} /> : null}
        {!result && loading[key] ? <LoadingBlock text={$t("common.loadingData")} /> : null}
        {!result && !loading[key] && !errors[key] ? <EmptyBlock text={$t("common.empty")} /> : null}
        {result ? (
          <>
            <FactStrip
              items={[
                { label: $t("inventory.snapshot"), value: formatNumber(metrics.snapshot_sku_count, 0) },
                { label: $t("inventory.stockValue"), value: formatCurrency(metrics.stock_value) },
                { label: $t("inventory.risk.stockout"), value: formatNumber(metrics.stockout_sku_count, 0), tone: "danger" },
                { label: $t("inventory.risk.low"), value: formatNumber(metrics.low_stock_sku_count, 0), tone: "warning" },
                { label: $t("inventory.risk.overstock"), value: formatNumber(metrics.overstock_sku_count, 0) },
                { label: $t("inventory.risk.slow"), value: formatNumber(metrics.slow_sku_count, 0) },
              ]}
            />
            <div className="metric-chart-grid metric-chart-grid--wide-left">
              <section className="metric-section metric-section--flush">
                <SectionHeading title={$t("inventory.riskTitle")} desc={$t("inventory.riskDesc")} />
                {inventoryRiskOption ? <Chart option={inventoryRiskOption} height={340} ariaLabel={$t("inventory.riskTitle")} /> : <EmptyBlock text={$t("common.empty")} />}
              </section>
              <section className="metric-section metric-section--flush">
                <SectionHeading title={$t("inventory.tableTitle")} />
                {inventoryRows.length ? (
                  <Table
                    className="metric-compact-table"
                    dataSource={inventoryRows}
                    rowKey={(row) => `${row.branch_id}-${row.item_id}-${row.dim_key}`}
                    pagination={false}
                    size="small"
                    scroll={{ x: 520 }}
                    columns={[
                      {
                        title: $t("inventory.item"),
                        dataIndex: "item_name",
                        key: "item_name",
                        render: (value, row) => value || row.dim_label || $t("common.unknown"),
                      },
                      { title: $t("inventory.stock"), dataIndex: "stock_qty", key: "stock_qty", align: "right", render: (value) => formatNumber(value, 1) },
                      { title: $t("inventory.sold7"), dataIndex: "sold_qty_7d", key: "sold_qty_7d", align: "right", render: (value) => formatNumber(value, 1) },
                      { title: $t("inventory.cover"), dataIndex: "days_cover_7d", key: "days_cover_7d", align: "right", render: (value) => (value === null ? "—" : `${formatNumber(value, 1)} 天`) },
                      {
                        title: $t("inventory.risk"),
                        dataIndex: "risk_level",
                        key: "risk_level",
                        render: (value) => <Tag color={value === "stockout" ? "error" : value === "low" ? "warning" : "default"}>{$t(`inventory.risk.${value}`)}</Tag>,
                      },
                    ]}
                  />
                ) : (
                  <EmptyBlock text={$t("common.empty")} />
                )}
              </section>
            </div>
          </>
        ) : null}
      </div>
    );
  };

  const renderPurchase = () => {
    const key = "purchase_performance:day";
    const result = getResult("purchase_performance", "day");
    const metrics = result?.metrics || {};
    return (
      <div>
        <SectionHeading title={$t("purchase.title")} desc={$t("purchase.desc")} />
        {errors[key] && !result ? <ErrorBlock $t={$t} onRetry={() => ensureView("purchase")} /> : null}
        {!result && loading[key] ? <LoadingBlock text={$t("common.loadingData")} /> : null}
        {!result && !loading[key] && !errors[key] ? <EmptyBlock text={$t("common.empty")} /> : null}
        {result ? (
          <>
            <FactStrip
              items={[
                { label: $t("purchase.order"), value: formatCurrency(metrics.purchase_order_amount) },
                { label: $t("purchase.receipt"), value: formatCurrency(metrics.receipt_amount) },
                { label: $t("purchase.return"), value: formatCurrency(metrics.purchase_return_amount) },
                { label: $t("purchase.net"), value: formatCurrency(metrics.net_receipt_amount) },
                { label: $t("purchase.top5"), value: formatPercent(metrics.supplier_top5_share), hint: $t("purchase.top5Hint") },
              ]}
            />
            <div className="metric-chart-grid">
              <section className="metric-section metric-section--flush">
                <SectionHeading title={$t("chart.orderAmount")} desc={$t("purchase.desc")} />
                {purchaseTrendOption ? <Chart option={purchaseTrendOption} height={340} ariaLabel={$t("chart.orderAmount")} /> : <EmptyBlock text={$t("common.empty")} />}
              </section>
              <section className="metric-section metric-section--flush">
                <SectionHeading title={$t("purchase.supplierTitle")} desc={$t("purchase.supplierDesc")} />
                {supplierRows.length ? (
                  <Table
                    className="metric-compact-table"
                    dataSource={supplierRows}
                    rowKey={(row) => row.supplier_id || row.dim_key}
                    pagination={false}
                    size="small"
                    scroll={{ x: 380 }}
                    columns={[
                      { title: $t("purchase.supplier"), dataIndex: "supplier_name", key: "supplier_name", render: (value, row) => value || row.dim_label || row.supplier_id },
                      { title: $t("purchase.netColumn"), dataIndex: "net_receipt_amount", key: "net_receipt_amount", align: "right", render: (value) => formatCurrency(value) },
                    ]}
                  />
                ) : loading["purchase_performance:supplier"] ? (
                  <LoadingBlock text={$t("common.loadingData")} />
                ) : (
                  <EmptyBlock text={$t("common.empty")} />
                )}
              </section>
            </div>
          </>
        ) : null}
      </div>
    );
  };

  const renderMarketing = () => {
    const key = "marketing_performance:promotion";
    const result = getResult("marketing_performance", "promotion");
    const metrics = result?.metrics || {};
    return (
      <div>
        <SectionHeading title={$t("marketing.title")} desc={$t("marketing.desc")} />
        {errors[key] && !result ? <ErrorBlock $t={$t} onRetry={() => ensureView("marketing")} /> : null}
        {!result && loading[key] ? <LoadingBlock text={$t("common.loadingData")} /> : null}
        {!result && !loading[key] && !errors[key] ? <EmptyBlock text={$t("common.empty")} /> : null}
        {result ? (
          <>
            <FactStrip
              items={[
                { label: $t("marketing.discount"), value: formatCurrency(metrics.marketing_discount_amount) },
                { label: $t("marketing.sales"), value: formatCurrency(metrics.marketing_sales_amount) },
                { label: $t("marketing.original"), value: formatCurrency(metrics.marketing_original_amount) },
                { label: $t("marketing.link"), value: formatPercent(metrics.promotion_link_rate) },
              ]}
            />
            <div className="metric-chart-grid metric-chart-grid--wide-left">
              <section className="metric-section metric-section--flush">
                <SectionHeading title={$t("marketing.activityTitle")} desc={$t("marketing.activityDesc")} />
                {marketingOption ? <Chart option={marketingOption} height={360} ariaLabel={$t("marketing.activityTitle")} /> : <EmptyBlock text={$t("common.empty")} />}
              </section>
              <section className="metric-section metric-section--flush">
                <SectionHeading title={$t("marketing.activityTitle")} />
                {marketingRows.length ? (
                  <Table
                    className="metric-compact-table"
                    dataSource={marketingRows}
                    rowKey={(row) => row.dim_key}
                    pagination={false}
                    size="small"
                    scroll={{ x: 460 }}
                    columns={[
                      { title: $t("marketing.activity"), dataIndex: "dim_label", key: "dim_label", render: (value, row) => value || row.dim_key },
                      { title: $t("chart.discount"), dataIndex: "marketing_discount_amount", key: "marketing_discount_amount", align: "right", render: (value) => formatCurrency(value) },
                      { title: $t("marketing.salesColumn"), dataIndex: "marketing_sales_amount", key: "marketing_sales_amount", align: "right", render: (value) => formatCurrency(value) },
                    ]}
                  />
                ) : (
                  <EmptyBlock text={$t("common.empty")} />
                )}
              </section>
            </div>
          </>
        ) : null}
      </div>
    );
  };

  const dictionaryMetrics = useMemo(() => {
    if (!catalog?.metrics) return [];
    const query = dictQuery.trim().toLowerCase();
    return catalog.metrics.filter((metric) => {
      if (dictGroup !== "all" && metric.group !== dictGroup) return false;
      if (!query) return true;
      return `${metric.name} ${metric.code} ${metric.formula}`.toLowerCase().includes(query);
    });
  }, [catalog, dictGroup, dictQuery]);

  const renderDictionary = () => {
    if (!catalog) {
      return initError ? (
        <ErrorBlock
          $t={$t}
          onRetry={() => {
            setInitError(false);
            setInitToken((value) => value + 1);
          }}
        />
      ) : (
        <LoadingBlock text={$t("common.loading")} />
      );
    }
    return (
      <div>
        <SectionHeading
          title={$t("dictionary.title")}
          desc={$t("dictionary.desc", {
            version: catalog.definitionVersion,
            count: catalog.metrics.length,
          })}
        />
        <div className="metric-dictionary__controls">
          <Input.Search
            allowClear
            value={dictQuery}
            onChange={(event) => setDictQuery(event.target.value)}
            placeholder={$t("dictionary.search")}
          />
          <Select
            value={dictGroup}
            onChange={setDictGroup}
            options={[
              { value: "all", label: $t("common.all") },
              ...(catalog.groups || []).map((group) => ({ value: group.code, label: group.name })),
            ]}
            style={{ minWidth: 160 }}
          />
          <span className="metric-dictionary__count">{$t("dictionary.count", { count: dictionaryMetrics.length })}</span>
        </div>
        <p className="metric-dictionary__hint">{$t("dictionary.valuesHint")}</p>
        {dictionaryMetrics.length ? (
          <MetricTable
            metrics={dictionaryMetrics}
            values={valuesByMetric}
            groups={catalog.groups}
            $t={$t}
            onSelect={setSelectedMetric}
          />
        ) : (
          <EmptyBlock text={$t("dictionary.empty")} />
        )}
      </div>
    );
  };

  const renderActive = () => {
    if (activeTab === "overview") return renderOverview();
    if (activeTab === "sales") return renderSales();
    if (activeTab === "inventory") return renderInventory();
    if (activeTab === "purchase") return renderPurchase();
    if (activeTab === "marketing") return renderMarketing();
    return renderDictionary();
  };

  const anyLoading = Object.values(loading).some(Boolean);

  return (
    <div className="metric-page">
      <header className="metric-controls">
        <div className="metric-controls__identity">
          <div className="metric-controls__title-row">
            <h1>{$t("page.title")}</h1>
            <Tag bordered={false}>{$t("page.definition", { version: catalog?.definitionVersion || "retail-metrics-v1.0.0" })}</Tag>
          </div>
          <p>{$t("page.subtitle")}</p>
          <div className="metric-controls__meta">
            <span>{$t("page.context")}</span>
            <span>{$t("page.asOf", { date: latestDate || "—" })}</span>
          </div>
        </div>
        <div className="metric-controls__actions">
          <div className="metric-controls__field">
            <span>{$t("page.range.label")}</span>
            <Segmented
              value={rangeDays || undefined}
              onChange={applyPreset}
              options={[
                { value: 7, label: $t("page.range.7") },
                { value: 30, label: $t("page.range.30") },
                { value: 90, label: $t("page.range.90") },
              ]}
            />
          </div>
          <div className="metric-controls__field">
            <span>{$t("page.range.custom")}</span>
            <RangePicker
              value={dates}
              allowClear={false}
              disabledDate={(current) => current && current > dayjs(latestDate || undefined)}
              onChange={(value) => {
                if (value?.[0] && value?.[1]) {
                  setRangeDays(null);
                  setDates(value);
                }
              }}
            />
          </div>
          <div className="metric-controls__field">
            <span>{$t("page.branch.label")}</span>
            <Select
              allowClear
              value={branchId}
              placeholder={$t("page.branch.all")}
              onChange={setBranchId}
              options={branchOptions}
              style={{ minWidth: 150 }}
            />
          </div>
          <Tooltip title={$t("page.action.refresh")}>
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              loading={anyLoading}
              onClick={handleRefresh}
              aria-label={$t("page.action.refresh")}
            >
              {$t("page.action.refresh")}
            </Button>
          </Tooltip>
        </div>
      </header>

      {initError ? (
        <Alert
          className="metric-init-alert"
          type="warning"
          showIcon
          message={$t("common.partialError")}
          closable
        />
      ) : null}

      {coverage ? (
        <CoverageStrip
          coverage={coverage}
          warnings={warnings}
          $t={$t}
          onOpenMetric={openMetricByCode}
        />
      ) : (
        <div className="metric-coverage metric-coverage--loading">
          <Skeleton active title={false} paragraph={{ rows: 1 }} />
        </div>
      )}

      <nav className="metric-tabs" aria-label={$t("page.title")}>
        <Segmented
          block
          value={activeTab}
          onChange={(value) => setActiveTab(value)}
          options={[
            { value: "overview", label: $t("tabs.overview") },
            { value: "sales", label: $t("tabs.sales") },
            { value: "inventory", label: $t("tabs.inventory") },
            { value: "purchase", label: $t("tabs.purchase") },
            { value: "marketing", label: $t("tabs.marketing") },
            { value: "dictionary", label: $t("tabs.dictionary") },
          ]}
        />
      </nav>

      <main className="metric-main">
        {renderActive()}
        {Object.keys(errors).length ? (
          <p className="metric-partial-error">{$t("common.partialError")}</p>
        ) : null}
      </main>

      <footer className="metric-footer">{$t("footer.note")}</footer>

      <MetricDrawer
        metric={selectedMetric}
        value={selectedValue}
        group={selectedMetric ? groupMap.get(selectedMetric.group) : null}
        $t={$t}
        definitionVersion={catalog?.definitionVersion || "retail-metrics-v1.0.0"}
        onClose={() => setSelectedMetric(null)}
      />
    </div>
  );
};

const App = () => {
  const appTheme = useAppTheme();
  return (
    <ConfigProvider theme={getThemeConfig(appTheme)}>
      <AppContent />
    </ConfigProvider>
  );
};

export default App;
