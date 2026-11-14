import React, { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Card, ConfigProvider, Empty, Spin, Tag, theme as antdTheme } from "antd";
import { useAppTheme, useSdkClient, useNavigate, useLocation, useI18n } from "@/context/app-context";
import { locales } from "../locales";
import { REPORT } from "../data/report";
import { buildFourWeekDailySeries, buildFourWeekTrend, getFourWeekRange } from "../data/fourWeekSales";
import { FOUR_WEEK_DAILY_SQL_CODE } from "../data/sqlResources";
import Chart from "../components/Chart";
import { Callout, KpiGrid, Panel, ReportTable, Section } from "../components/Ui";
import "./index.css";

const formatMoney = (value, digits = 2) =>
  `¥${Number(value || 0).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
const formatInt = (value) => Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 0 });
const formatPct = (value, digits = 2) => `${Number(value || 0).toFixed(digits)}%`;
const formatWan = (value) => `${(Number(value || 0) / 10000).toFixed(2)} 万`;
const signedMoney = (value) => (Number(value) < 0 ? `-¥${Math.abs(Number(value)).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : formatMoney(value));
const changePct = (current, previous) => (Number(previous) ? (Number(current) / Number(previous) - 1) * 100 : 0);
const isThemeColor = (value) => /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(value || "");
const getThemeConfig = (appTheme) => ({
  cssVar: { key: "customer-product-report-theme" },
  algorithm: appTheme?.mode === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
  ...(isThemeColor(appTheme?.skinColor) ? { token: { colorPrimary: appTheme.skinColor } } : {}),
});
const axisStyle = (token) => ({
  axisLine: { lineStyle: { color: token.colorBorderSecondary } },
  axisLabel: { color: token.colorTextSecondary },
  splitLine: { lineStyle: { color: token.colorBorderSecondary } },
  nameTextStyle: { color: token.colorTextSecondary },
});

const createFourWeekOption = (rows, $t, token) => {
  const rowByDate = new Map(rows.map((row) => [row.date, row]));
  return {
    color: [token.colorPrimary],
    tooltip: {
      trigger: "axis",
      formatter: (params) => {
        const point = Array.isArray(params) ? params[0] : params;
        const date = point?.axisValue || point?.name || "";
        const row = rowByDate.get(date);
        if (!row || row.salesAmount === null) {
          return `${date}<br/>${$t("overview.fourWeek.noData")}`;
        }
        return `${date}<br/>${$t("overview.fourWeek.sales")}: ${formatMoney(row.salesAmount)}`;
      },
    },
    grid: { left: 12, right: 16, top: 24, bottom: 12, containLabel: true },
    xAxis: {
      type: "category",
      data: rows.map((row) => row.date),
      ...axisStyle(token),
      axisLabel: {
        color: token.colorTextSecondary,
        hideOverlap: true,
        formatter: (value) => value.slice(5),
      },
    },
    yAxis: {
      type: "value",
      name: $t("overview.fourWeek.salesWan"),
      ...axisStyle(token),
    },
    legend: {
      data: [$t("overview.fourWeek.sales"), $t("overview.fourWeek.trend")],
      textStyle: { color: token.colorTextSecondary },
    },
    series: [
      {
        name: $t("overview.fourWeek.sales"),
        type: "line",
        smooth: true,
        connectNulls: false,
        showSymbol: true,
        symbolSize: 5,
        data: rows.map((row) =>
          row.salesAmount === null ? null : Number((row.salesAmount / 10000).toFixed(4)),
        ),
      },
      {
        name: $t("overview.fourWeek.trend"),
        type: "line",
        smooth: false,
        showSymbol: false,
        lineStyle: { type: "dashed", width: 2 },
        data: buildFourWeekTrend(rows).map((value) =>
          value === null ? null : Number((value / 10000).toFixed(4)),
        ),
      },
    ],
  };
};

const App = () => {
  const client = useSdkClient();
  const $i18n = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const appTheme = useAppTheme();
  const { token } = antdTheme.useToken();
  $i18n.addLocale(locales);
  const $t = (key, vars) => {
    const text = $i18n.t(key);
    if (!vars) return text;
    return String(text ?? "").replace(/\{(\w+)\}/g, (match, name) =>
      vars[name] === undefined || vars[name] === null ? match : String(vars[name]),
    );
  };
  const [fourWeek, setFourWeek] = useState({
    status: "loading",
    range: null,
    rows: [],
  });
  const fourWeekRequestRef = useRef(0);

  const loadFourWeekSales = useCallback(async () => {
    const requestId = fourWeekRequestRef.current + 1;
    fourWeekRequestRef.current = requestId;
    setFourWeek({ status: "loading", range: null, rows: [] });
    try {
      const latestDate = REPORT.meta.end;
      const range = getFourWeekRange(latestDate);
      if (!range) {
        setFourWeek({ status: "empty", range: null, rows: [] });
        return;
      }
      if (requestId !== fourWeekRequestRef.current) return;

      const dailyResult = await client.sql.execute({
        sqlCode: FOUR_WEEK_DAILY_SQL_CODE,
        params: range,
      });
      if (!dailyResult?.execSuccess || !Array.isArray(dailyResult.execResult)) {
        throw new Error("four-week daily sales query failed");
      }
      if (requestId !== fourWeekRequestRef.current) return;

      setFourWeek({
        status: "success",
        range,
        rows: buildFourWeekDailySeries(latestDate, dailyResult.execResult),
      });
    } catch (error) {
      console.error("load four-week daily sales failed", error);
      if (requestId === fourWeekRequestRef.current) {
        setFourWeek({ status: "error", range: null, rows: [] });
      }
    }
  }, [client]);

  useEffect(() => {
    void loadFourWeekSales();
    return () => {
      fourWeekRequestRef.current += 1;
    };
  }, [loadFourWeekSales]);

  const k = REPORT.kpis;
  const m = REPORT.member;
  const h = REPORT.hero;
  const margin = REPORT.margin;
  const scrollTo = (id) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const navItems = ["executive", "overview", "basket", "top15", "hero", "campaign", "member", "margin", "swot", "roadmap", "method"];
  const col = (title, dataIndex, render, className = "") => ({ title, dataIndex, key: dataIndex, className, render });
  const moneyCol = (title, dataIndex) => col(title, dataIndex, (value) => formatMoney(value), "number");

  const dailyOption = {
    color: [token.colorPrimary, token.colorWarning],
    tooltip: { trigger: "axis" },
    legend: { data: [$t("overview.chart.salesWan"), $t("overview.chart.orders")], textStyle: { color: token.colorTextSecondary } },
    grid: { left: 12, right: 16, top: 42, bottom: 12, containLabel: true },
    xAxis: { type: "category", data: REPORT.daily.map((r) => r.date), ...axisStyle(token) },
    yAxis: [
      { type: "value", name: $t("overview.chart.salesWan"), ...axisStyle(token) },
      { type: "value", name: $t("overview.chart.orders"), ...axisStyle(token), splitLine: { show: false } },
    ],
    series: [
      { name: $t("overview.chart.salesWan"), type: "line", smooth: true, data: REPORT.daily.map((r) => Number((r.sales / 10000).toFixed(2))) },
      { name: $t("overview.chart.orders"), type: "bar", yAxisIndex: 1, barMaxWidth: 18, data: REPORT.daily.map((r) => r.orders) },
    ],
  };
  const hourOption = {
    color: [token.colorPrimary],
    tooltip: { trigger: "axis", formatter: (params) => `${params[0].name}: ${formatMoney(REPORT.hours[params[0].dataIndex].sales)}` },
    grid: { left: 12, right: 12, top: 24, bottom: 12, containLabel: true },
    xAxis: { type: "category", data: REPORT.hours.map((r) => `${r.hour}:00`), ...axisStyle(token) },
    yAxis: { type: "value", name: $t("common.money"), ...axisStyle(token) },
    series: [{ type: "bar", barMaxWidth: 28, data: REPORT.hours.map((r) => ({ value: r.sales, itemStyle: { color: r.hour >= 17 && r.hour <= 21 ? token.colorWarning : token.colorPrimary } })) }],
  };
  const bucketOption = {
    color: [token.colorPrimary],
    tooltip: { trigger: "axis", formatter: (params) => `${params[0].name}: ${formatPct(params[0].value)}` },
    grid: { left: 12, right: 12, top: 24, bottom: 12, containLabel: true },
    xAxis: { type: "category", data: REPORT.buckets.map((r) => r.label), ...axisStyle(token), axisLabel: { ...axisStyle(token).axisLabel, rotate: 25 } },
    yAxis: { type: "value", name: $t("common.share"), ...axisStyle(token) },
    series: [{ type: "bar", barMaxWidth: 32, data: REPORT.buckets.map((r) => r.countShare), label: { show: true, position: "top", formatter: (p) => formatPct(p.value), color: token.colorTextSecondary } }],
  };
  const top15Option = {
    color: [token.colorPrimary],
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (params) => `${params[0].name}<br/>${formatMoney(params[0].value)}` },
    grid: { left: 12, right: 64, top: 12, bottom: 12, containLabel: true },
    xAxis: { type: "value", name: $t("common.money"), ...axisStyle(token) },
    yAxis: { type: "category", data: REPORT.top15.map((r) => r.name).reverse(), ...axisStyle(token) },
    series: [{ type: "bar", barMaxWidth: 18, data: REPORT.top15.map((r) => r.sales).reverse(), label: { show: true, position: "right", formatter: (p) => formatMoney(p.value, 0), color: token.colorTextSecondary } }],
  };
  const heroDailyOption = {
    color: [token.colorPrimary],
    tooltip: { trigger: "axis" },
    grid: { left: 12, right: 12, top: 24, bottom: 12, containLabel: true },
    xAxis: { type: "category", data: h.daily.map((r) => r.date), ...axisStyle(token) },
    yAxis: { type: "value", name: $t("common.qty"), ...axisStyle(token) },
    series: [{ type: "line", smooth: true, areaStyle: { opacity: 0.08 }, data: h.daily.map((r) => r.qty) }],
  };
  const attachOption = {
    color: [token.colorPrimary],
    tooltip: { trigger: "axis", formatter: (params) => `${params[0].name}: ${formatPct(params[0].value)}` },
    grid: { left: 12, right: 12, top: 24, bottom: 12, containLabel: true },
    xAxis: { type: "category", data: [$t("hero.kpi.attach"), ...h.periods.map((r) => r.label)], ...axisStyle(token) },
    yAxis: { type: "value", name: $t("common.rate"), max: 35, ...axisStyle(token) },
    series: [{ type: "bar", barMaxWidth: 52, data: [h.attachRate, ...h.periods.map((r) => r.attachRate)], label: { show: true, position: "top", formatter: (p) => formatPct(p.value), color: token.colorTextSecondary } }],
  };

  const comparisonColumns = [
    col($t("overview.compareTitle"), "label"),
    col($t("common.current14"), "current", (value, row) => row.format === "integer" ? formatInt(value) : formatMoney(value), "number"),
    col($t("common.previous14"), "previous", (value, row) => row.format === "integer" ? formatInt(value) : formatMoney(value), "number"),
    col($t("common.change"), "change", (_, row) => <span className={changePct(row.current, row.previous) >= 0 ? "report-positive" : "report-negative"}>{formatPct(changePct(row.current, row.previous))}</span>, "number"),
  ];
  const bucketColumns = [
    col($t("basket.table.range"), "label"),
    col($t("common.orders"), "count", formatInt, "number"),
    col($t("basket.table.orderShare"), "countShare", formatPct, "number"),
    moneyCol($t("common.sales"), "sales"),
    col($t("basket.table.salesShare"), "salesShare", formatPct, "number"),
  ];
  const top15Columns = [
    col("#", "rank"),
    col($t("common.product"), "name"),
    moneyCol($t("common.sales"), "sales"),
    col($t("common.qty"), "qty", (v) => formatInt(v), "number"),
    moneyCol($t("common.averagePrice"), "avgPrice"),
    col($t("common.archiveMargin"), "archiveMargin", (v) => v === null ? $t("common.unmaintained") : <span className={v < 0 ? "report-negative" : ""}>{formatPct(v, 1)}</span>, "number"),
  ];
  const activityColumns = [
    col($t("common.activityCode"), "code"),
    col($t("common.activityName"), "name"),
    col($t("common.flowLines"), "lines", formatInt, "number"),
    moneyCol($t("common.paidSales"), "sales"),
    moneyCol($t("common.discountAmount"), "discount"),
  ];
  const periodColumns = [
    col($t("hero.period"), "label"),
    col($t("hero.periodQty"), "qty", (v, row) => `${formatInt(v)} / ${formatPct(row.qtyShare)}`, "number"),
    col($t("common.mainTickets"), "receipts", formatInt, "number"),
    col($t("common.otherProducts"), "attached", (v, row) => `${formatInt(v)} / ${formatPct(row.attachRate)}`, "number"),
    moneyCol($t("common.singleAov"), "singleAov"),
    moneyCol($t("common.attachedAov"), "attachedAov"),
  ];
  const candidateColumns = [
    col($t("common.product"), "name"),
    moneyCol($t("common.price"), "price"),
    moneyCol($t("common.archiveCost"), "cost"),
    moneyCol($t("common.unitGp"), "unitGp"),
    col($t("common.archiveMargin"), "margin", (v) => formatPct(v, 1), "number"),
    col($t("common.attachCount"), "attachCount", (v) => v, "number"),
    col($t("common.selection"), "role"),
  ];
  const comboColumns = [
    col($t("campaign.execute.action"), "name", (v, row) => <><strong>{v}</strong><br/><Tag>{row.time}</Tag></>),
    col($t("common.combo"), "components"),
    col($t("common.listSuggested"), "price", (v, row) => <span>{formatMoney(row.listPrice)} → <strong>{formatMoney(v)}</strong></span>, "number"),
    moneyCol($t("common.cost"), "cost"),
    col($t("common.gpMargin"), "gp", (v, row) => `${formatMoney(v)} / ${formatPct(row.margin)}`, "number"),
    col($t("common.increment"), "increment", (v) => <span className="report-positive">+{formatMoney(v)}</span>, "number"),
  ];
  const scenarioColumns = [
    col($t("common.scenario"), "label"),
    col($t("common.targetRate"), "rates", (_, row) => `${formatPct(row.lunchRate, 0)} / ${formatPct(row.doubleRate, 0)} / ${formatPct(row.teaRate, 0)}`, "number"),
    col($t("common.monthUnits"), "units", (v) => `${formatInt(v)}份`, "number"),
    moneyCol($t("common.monthRevenue"), "revenue"),
    moneyCol($t("common.monthGp"), "gp"),
    col($t("common.monthIncrement"), "increment", (v) => <span className="report-positive">+{formatMoney(v)}</span>, "number"),
  ];
  const negativeColumns = [
    col($t("common.product"), "name"),
    moneyCol($t("common.sales"), "sales"),
    col($t("common.grossProfit"), "gp", (v) => <span className="report-negative">{signedMoney(v)}</span>, "number"),
    col($t("common.coveredMargin"), "margin", (v) => <span className="report-negative">{formatPct(v, 1)}</span>, "number"),
  ];
  const executeRows = [1, 2, 3, 4].map((i) => ({ key: i, action: $t(`campaign.execute${i}`), how: $t(`campaign.execute${i}.action`), acceptance: $t(`campaign.execute${i}.acceptance`) }));
  const executeColumns = [col($t("campaign.execute.action"), "action"), col($t("common.action"), "how"), col($t("common.acceptance"), "acceptance")];
  const swotGroups = ["s", "w", "o", "t"].map((key) => ({ key, title: $t(`swot.${key}`), desc: $t(`swot.${key}.desc`), items: [1, 2, 3, 4].map((i) => $t(`swot.${key}${i}`)) }));
  const roadmapGroups = [1, 2, 3, 4].map((i) => ({ key: i, week: $t(`roadmap.${i}`), title: $t(`roadmap.${i}.title`), items: [1, 2, 3, 4].map((j) => $t(`roadmap.${i}.${j}`)), goal: $t(`roadmap.${i}.goal`) }));

  const fourWeekOption = fourWeek.status === "success"
    ? createFourWeekOption(fourWeek.rows, $t, token)
    : null;

  return (
    <ConfigProvider theme={getThemeConfig(appTheme)}>
      <div className="report-container">
        <header className="report-cover">
          <div className="report-cover__eyebrow">{$t("report.eyebrow")}</div>
          <h1>{$t("report.title")}</h1>
          <div className="report-cover__sub">{$t("report.subtitle")}</div>
          <div className="report-cover__meta">{$t("report.meta", REPORT.meta)}</div>
          <div className="report-cover__tags">
            <Tag>{$t("report.tag.orders", { count: formatInt(REPORT.meta.orderHeaders) })}</Tag>
            <Tag>{$t("report.tag.flows", { count: formatInt(REPORT.meta.flowLines) })}</Tag>
            <Tag>{$t("report.tag.top15")}</Tag>
            <Tag>{$t("report.tag.scenarios")}</Tag>
          </div>
        </header>
        <nav className="report-nav">
          {navItems.map((id) => <Button key={id} type="link" onClick={() => scrollTo(id)}>{$t(`report.nav.${id}`)}</Button>)}
        </nav>

        <div className="report-grid report-grid--half report-top-trend">
          <Panel title={$t("overview.fourWeek.title")}>
            {fourWeek.status === "loading" ? (
              <div className="report-chart-state">
                <Spin size="small" />
                <span>{$t("overview.fourWeek.loading")}</span>
              </div>
            ) : fourWeek.status === "error" ? (
              <div className="report-chart-state">
                <Alert
                  type="error"
                  showIcon
                  message={$t("overview.fourWeek.errorTitle")}
                  description={$t("overview.fourWeek.errorDesc")}
                  action={
                    <Button size="small" onClick={() => void loadFourWeekSales()}>
                      {$t("overview.fourWeek.retry")}
                    </Button>
                  }
                />
              </div>
            ) : fourWeek.status === "empty" ? (
              <div className="report-chart-state">
                <Empty description={$t("overview.fourWeek.empty")} />
              </div>
            ) : (
              <>
                <Chart option={fourWeekOption} height={280} />
                <p className="report-panel__desc">
                  {$t("overview.fourWeek.desc", {
                    start: fourWeek.range.startDate,
                    end: fourWeek.range.endDate,
                    reportStart: REPORT.meta.start,
                    reportEnd: REPORT.meta.end,
                  })}
                </p>
              </>
            )}
          </Panel>
        </div>

        <Section id="executive" number="0" title={$t("executive.title")} intro={$t("executive.intro")}>
          <div className="report-grid report-grid--three">
            <Panel title={$t("executive.stateTitle")}>
              <div className="executive-card__headline">{$t("executive.stateHeadline")}</div>
              <p className="report-panel__desc">{$t("executive.stateDesc", {
                salesChange: formatPct(k.salesChange),
                ordersChange: formatPct(k.ordersChange),
                aovChange: formatPct(changePct(k.aov, k.prevAov)),
              })}</p>
            </Panel>
            <Panel title={$t("executive.opportunityTitle")}>
              <div className="executive-card__headline">{$t("executive.opportunityHeadline")}</div>
              <p className="report-panel__desc">{$t("executive.opportunityDesc", {
                qtyChange: formatPct(h.qtyChange),
                attachRate: formatPct(h.attachRate),
                attachedAov: formatMoney(h.attachedAov),
              })}</p>
            </Panel>
            <Panel title={$t("executive.riskTitle")}>
              <div className="executive-card__headline">{$t("executive.riskHeadline")}</div>
              <p className="report-panel__desc">{$t("executive.riskDesc", {
                coverage: formatPct(margin.costCoverage),
                negativeCount: margin.negativeCount,
              })}</p>
            </Panel>
          </div>
          <Callout type="info" title={$t("executive.actionTitle")}>
            {$t("executive.actionDesc")}
          </Callout>
        </Section>

        <Section id="overview" number="1" title={$t("overview.title")} intro={$t("overview.intro")}>
          <KpiGrid items={[
            { value: formatWan(k.sales), label: $t("overview.kpi.sales"), hint: $t("overview.kpi.salesHint", { value: k.salesChange.toFixed(2) }) },
            { value: formatWan(k.orders), label: $t("overview.kpi.orders"), hint: $t("overview.kpi.salesHint", { value: k.ordersChange.toFixed(2) }), tone: "error" },
            { value: formatMoney(k.aov), label: $t("overview.kpi.aov"), hint: $t("overview.kpi.aovHint", { value: formatMoney(k.prevAov) }) },
            { value: formatMoney(k.median), label: $t("overview.kpi.median"), hint: $t("overview.kpi.medianHint"), tone: "warning" },
            { value: formatPct(k.memberSalesShare), label: $t("overview.kpi.member"), hint: $t("overview.kpi.memberHint", { value: formatPct(k.memberOrderShare) }), tone: "success" },
            { value: formatPct(k.eveningShare), label: $t("overview.kpi.evening"), hint: $t("overview.kpi.eveningHint", { value: formatWan(k.peakHourSales) }) },
          ]} />
          <div className="report-grid report-grid--wide">
            <Panel title={$t("overview.dailyTitle")}><Chart option={dailyOption} height={320}/><p className="report-panel__desc">{$t("overview.dailyDesc")}</p></Panel>
            <Panel title={$t("overview.compareTitle")}><ReportTable columns={comparisonColumns} data={REPORT.comparison.map((r) => ({ ...r, key: r.label }))} rowKey="key"/><p className="report-panel__desc">{$t("overview.compareDesc")}</p></Panel>
          </div>
          <Panel title={$t("overview.hourTitle")} wide><Chart option={hourOption} height={300}/><p className="report-panel__desc">{$t("overview.hourDesc", { weekend: formatMoney(k.weekendSalesDay), lift: k.weekendLift.toFixed(2), weekendAov: formatMoney(k.weekendAov), weekdayAov: formatMoney(k.weekdayAov) })}</p></Panel>
        </Section>

        <Section id="basket" number="2" title={$t("basket.title")} intro={$t("basket.intro")}>
          <KpiGrid compact items={[
            { value: formatPct(k.under20Share), label: $t("basket.kpi.under20"), hint: $t("basket.kpi.under20Hint", { count: formatInt(k.under20Count) }), tone: "warning" },
            { value: formatMoney(k.median), label: $t("basket.kpi.median"), hint: $t("basket.kpi.medianHint", { value: formatMoney(k.aov) }) },
            { value: formatPct(k.largeSalesShare), label: $t("basket.kpi.large"), hint: $t("basket.kpi.largeHint", { value: formatPct(k.largeOrderShare) }) },
            { value: formatPct(k.top10SalesShare), label: $t("basket.kpi.top10"), hint: $t("basket.kpi.top10Hint"), tone: "success" },
          ]} />
          <div className="report-grid report-grid--wide">
            <Panel title={$t("basket.chartTitle")}><Chart option={bucketOption} height={320}/><p className="report-panel__desc">{$t("basket.chartDesc")}</p></Panel>
            <Panel title={$t("basket.tiersTitle")}><ReportTable columns={[col($t("basket.tier"), "tier"), col($t("basket.scene"), "scene"), col($t("basket.action"), "action")]} data={[1,2,3,4].map((i) => ({ key: i, tier: $t(`basket.tier${i}`), scene: $t(`basket.tier${i}.scene`), action: $t(`basket.tier${i}.action`) }))} rowKey="key"/></Panel>
          </div>
          <ReportTable columns={bucketColumns} data={REPORT.buckets.map((r) => ({ ...r, key: r.label }))} rowKey="key"/>
        </Section>

        <Section id="top15" number="3" title={$t("top15.title")} intro={$t("top15.intro")}>
          <div className="report-grid report-grid--wide">
            <Panel title={$t("top15.chartTitle")}><Chart option={top15Option} height={520}/><p className="report-panel__desc">{$t("top15.chartDesc")}</p></Panel>
            <Panel title={$t("top15.rolesTitle")}>
              <Callout type="success" title={$t("top15.role1")}>{$t("top15.role1Desc")}</Callout>
              <Callout type="info" title={$t("top15.role2")}>{$t("top15.role2Desc")}</Callout>
              <Callout type="warning" title={$t("top15.role3")}>{$t("top15.role3Desc")}</Callout>
            </Panel>
          </div>
          <ReportTable columns={top15Columns} data={REPORT.top15.map((r) => ({ ...r, key: r.rank }))} rowKey="key"/>
          <p className="report-note">{$t("top15.footer")}</p>
        </Section>

        <Section id="hero" number="4" title={$t("hero.title")} intro={$t("hero.intro")}>
          <h3>{$t("hero.processTitle")}</h3>
          <p className="report-panel__desc">{$t("hero.processIntro")}</p>
          <div className="report-process">
            <Card className="report-process__item" title={$t("hero.processDiscoverTitle")}>
              <div className="report-process__step">01 · {$t("hero.processDiscoverLabel")}</div>
              <p className="report-process__text">{$t("hero.processDiscoverDesc", { qtyChange: formatPct(h.qtyChange), sales: formatMoney(h.sales) })}</p>
            </Card>
            <Card className="report-process__item" title={$t("hero.processDefineTitle")}>
              <div className="report-process__step">02 · {$t("hero.processDefineLabel")}</div>
              <p className="report-process__text">{$t("hero.processDefineDesc", { attachRate: formatPct(h.attachRate), singleAov: formatMoney(h.singleAov), attachedAov: formatMoney(h.attachedAov) })}</p>
            </Card>
            <Card className="report-process__item" title={$t("hero.processReasonTitle")}>
              <div className="report-process__step">03 · {$t("hero.processReasonLabel")}</div>
              <p className="report-process__text">{$t("hero.processReasonDesc")}</p>
            </Card>
            <Card className="report-process__item" title={$t("hero.processConclusionTitle")}>
              <div className="report-process__step">04 · {$t("hero.processConclusionLabel")}</div>
              <p className="report-process__text">{$t("hero.processConclusionDesc")}</p>
            </Card>
          </div>
          <KpiGrid compact items={[
            { value: formatMoney(h.sales), label: $t("hero.kpi.sales"), hint: $t("hero.kpi.salesHint"), tone: "success" },
            { value: `${formatInt(h.qty)}份`, label: $t("hero.kpi.qty"), hint: $t("hero.kpi.qtyHint", { value: h.qtyDaily.toFixed(1) }) },
            { value: formatPct(h.qtyChange), label: $t("hero.kpi.qtyChange"), hint: $t("hero.kpi.qtyChangeHint", { previous: h.prevQtyDaily.toFixed(1), current: h.qtyDaily.toFixed(1) }), tone: "error" },
            { value: formatPct(h.attachRate), label: $t("hero.kpi.attach"), hint: $t("hero.kpi.attachHint", { attached: h.attachCount, total: h.mainReceipts }), tone: "warning" },
          ]} />
          <div className="report-grid report-grid--wide">
            <Panel title={$t("hero.dailyTitle")}><Chart option={heroDailyOption} height={310}/><p className="report-panel__desc">{$t("hero.dailyDesc")}</p></Panel>
            <Panel title={$t("hero.unitTitle")}>
              <KpiGrid compact items={[
                { value: "¥16.90", label: $t("hero.price") },
                { value: "¥10.50", label: $t("hero.cost") },
                { value: "¥6.40", label: $t("hero.unitGp"), hint: $t("hero.unitGpHint"), tone: "success" },
              ]} />
              <p className="report-panel__desc">{$t("hero.recover", { value: formatMoney(h.recover190Gp) })}</p>
              <p className="report-note">{$t("hero.costNote")}</p>
            </Panel>
          </div>
          <h3>{$t("hero.periodTitle")}</h3>
          <ReportTable columns={periodColumns} data={h.periods.map((r) => ({ ...r, key: r.label }))} rowKey="key"/>
          <div className="report-grid">
            <Panel title={$t("hero.lunchTitle")}><p className="report-panel__desc">{$t("hero.lunchDesc")}</p></Panel>
            <Panel title={$t("hero.dinnerTitle")}><p className="report-panel__desc">{$t("hero.dinnerDesc")}</p></Panel>
          </div>
          <Panel title={$t("hero.attachTitle")} wide><Chart option={attachOption} height={270}/></Panel>
          <div className="report-grid report-grid--wide">
            <Panel title={$t("hero.attachTop")}><ReportTable columns={[col($t("common.product"), "name"), col($t("common.attachCount"), "count", formatInt, "number")]} data={h.attachTop.map((r) => ({ ...r, key: r.name }))} rowKey="key"/><p className="report-panel__desc">{$t("hero.attachTopDesc", { secondMeals: h.secondMeals, bags: h.bags })}</p></Panel>
            <Panel title={$t("hero.basketTitle")}>
              <KpiGrid compact items={[
                { value: formatMoney(h.singleAov), label: $t("hero.singleAov"), hint: $t("hero.singleAovHint", { count: formatInt(h.singleCount) }) },
                { value: formatMoney(h.attachedAov), label: $t("hero.attachedAov"), hint: $t("hero.attachedAovHint", { value: (h.attachedAov / h.singleAov * 100 - 100).toFixed(1) }), tone: "success" },
              ]} />
              <p className="report-panel__desc">{$t("hero.basketDesc")}</p><Callout type="info">{$t("hero.basketAction")}</Callout>
            </Panel>
          </div>
          <h3>{$t("hero.candidateTitle")}</h3>
          <ReportTable columns={candidateColumns} data={REPORT.candidates.map((r) => ({ ...r, key: r.name }))} rowKey="key"/>
          <Callout type="warning" title={$t("hero.candidateNote")}>{$t("hero.candidateNoteDesc")}</Callout>
        </Section>

        <Section id="campaign" number="5" title={$t("campaign.title")} intro={$t("campaign.intro")}>
          <ReportTable columns={comboColumns} data={REPORT.combos.map((r) => ({ ...r, key: r.key }))} rowKey="key"/>
          <div className="report-grid report-grid--three">
            <Panel title={$t("campaign.lunchTitle")}><p className="report-panel__desc">{$t("campaign.lunchDesc")}</p><p className="report-note">{$t("campaign.lunchBase")}</p></Panel>
            <Panel title={$t("campaign.doubleTitle")}><p className="report-panel__desc">{$t("campaign.doubleDesc")}</p><p className="report-note">{$t("campaign.doubleBase")}</p></Panel>
            <Panel title={$t("campaign.teaTitle")}><p className="report-panel__desc">{$t("campaign.teaDesc")}</p><p className="report-note">{$t("campaign.teaBase")}</p></Panel>
          </div>
          <h3>{$t("campaign.scenarioTitle")}</h3>
          <ReportTable columns={scenarioColumns} data={REPORT.scenarios.map((r) => ({ ...r, key: r.label }))} rowKey="key"/>
          <p className="report-note">{$t("campaign.scenarioDesc")}</p>
          <Callout type="success" title={$t("campaign.recoverTitle")}>{$t("campaign.recoverDesc", { value: formatMoney(h.recover190Gp) })}</Callout>
          <Callout type="warning" title={$t("campaign.boundaryTitle")}>{$t("campaign.boundaryDesc")}</Callout>
          <h3>{$t("campaign.executeTitle")}</h3>
          <ReportTable columns={executeColumns} data={executeRows} rowKey="key"/>
        </Section>

        <Section id="member" number="6" title={$t("member.title")} intro={$t("member.intro")}>
          <KpiGrid compact items={[
            { value: formatMoney(m.memberAov), label: $t("member.kpi.aov"), hint: $t("member.kpi.aovHint", { value: formatMoney(m.guestAov) }), tone: "success" },
            { value: formatPct(m.memberMarkdown), label: $t("member.kpi.markdown"), hint: $t("member.kpi.markdownHint", { value: formatPct(m.guestMarkdown) }) },
            { value: formatPct(m.promoLineShare), label: $t("member.kpi.promo"), hint: $t("member.kpi.promoHint", { count: formatInt(m.promoLines) }) },
            { value: "0", label: $t("member.kpi.coupon"), hint: $t("member.kpi.couponHint"), tone: "warning" },
          ]} />
          <div className="report-grid">
            <Panel title={$t("member.operationTitle")}><p className="report-panel__desc">{$t("member.operationDesc", { orders: formatInt(m.guestOrders), share: formatPct(m.guestOrderShare), selfOrders: formatInt(m.selfOrders), selfShare: formatPct(m.selfOrderShare), selfAov: formatMoney(m.selfAov), staffAov: formatMoney(m.staffAov) })}</p><Callout type="info">{$t("member.operationAction")}</Callout></Panel>
            <Panel title={$t("member.promoTitle")}><p className="report-panel__desc">{$t("member.promoDesc", { count: formatInt(m.promoLines), share: formatPct(m.promoLineShare), markdown: formatPct(m.markdown) })}</p><Callout type="info">{$t("member.promoAction")}</Callout></Panel>
          </div>
          <h3>{$t("member.activityTitle")}</h3>
          <ReportTable columns={activityColumns} data={REPORT.activities.map((r) => ({ ...r, key: r.code }))} rowKey="key"/>
          <p className="report-note">{$t("member.activityDesc")}</p>
        </Section>

        <Section id="margin" number="7" title={$t("margin.title")} intro={$t("margin.intro")}>
          <KpiGrid compact items={[
            { value: formatPct(margin.costCoverage), label: $t("margin.kpi.coverage"), hint: $t("margin.kpi.coverageHint", { value: formatPct(margin.prevCostCoverage) }), tone: "warning" },
            { value: formatPct(margin.coveredMargin), label: $t("margin.kpi.covered"), hint: $t("margin.kpi.coveredHint") },
            { value: `${margin.negativeCount}个`, label: $t("margin.kpi.negative"), hint: $t("margin.kpi.negativeHint", { value: signedMoney(margin.negativeGp) }), tone: "error" },
            { value: formatPct(margin.purchaseTop5), label: $t("margin.kpi.supplier"), hint: $t("margin.kpi.supplierHint") },
          ]} />
          <ReportTable columns={negativeColumns} data={REPORT.negative.map((r) => ({ ...r, key: r.name }))} rowKey="key"/>
          <p className="report-note">{$t("margin.note", { value: formatMoney(margin.negativeMonth) })}</p>
          <div className="report-grid report-grid--three">
            {[1,2,3].map((i) => <Panel key={i} title={$t(`margin.action${i}`)}><p className="report-panel__desc">{$t(`margin.action${i}Desc`)}</p></Panel>)}
          </div>
        </Section>

        <Section id="swot" number="8" title={$t("swot.title")}>
          <div className="report-swot">
            {swotGroups.map((group) => <Card className="report-swot__card" key={group.key} title={group.title}><p className="report-panel__desc">{group.desc}</p><ul>{group.items.map((item, i) => <li key={i}>{item}</li>)}</ul></Card>)}
          </div>
        </Section>

        <Section id="roadmap" number="9" title={$t("roadmap.title")}>
          <div className="report-roadmap">
            {roadmapGroups.map((group) => <Card key={group.key} title={group.week}><h4>{group.title}</h4><ul>{group.items.map((item, i) => <li key={i}>{item}</li>)}</ul><div className="report-roadmap__goal">{group.goal}</div></Card>)}
          </div>
        </Section>

        <Section id="method" number="10" title={$t("method.title")}>
          <Card><ul className="report-method">
            {["window", "sales", "margin", "member", "hero", "forecast", "inventory", "privacy"].map((key) => <li key={key}>{$t(`method.${key}`)}</li>)}
          </ul></Card>
          <Callout type="info">{$t("method.sources")}</Callout>
        </Section>
        <footer className="report-footer">{$t("footer")}</footer>
      </div>
    </ConfigProvider>
  );
};

export default App;
