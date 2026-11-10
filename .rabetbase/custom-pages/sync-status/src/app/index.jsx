import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  ConfigProvider,
  Descriptions,
  Drawer,
  Empty,
  Input,
  Segmented,
  Select,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  theme as antdTheme,
} from "antd";
import {
  ArrowRightOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  ExclamationCircleFilled,
  InfoCircleOutlined,
  ReloadOutlined,
  RightOutlined,
  SearchOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import dayjs from "dayjs";
import { useAppTheme, useI18n, useSdkClient } from "@/context/app-context";
import { locales } from "../locales";
import "./index.css";

const STATUS_MODEL = "dataset_eac713d6c72646c9b2b3a2ee0f8e31eb";
const EVENT_MODEL = "dataset_b864ca67ee41418c9a6c3a189b726be6";
const PAGE_SIZE = 20;
const AUTO_REFRESH_MS = 5 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

const STATUS_META = {
  success: { color: "success", tone: "success", label: "statusSuccess" },
  partial: { color: "warning", tone: "warning", label: "statusPartial" },
  failed: { color: "error", tone: "danger", label: "statusFailed" },
  interrupted: { color: "warning", tone: "warning", label: "statusInterrupted" },
  running: { color: "processing", tone: "processing", label: "statusRunning" },
  retry_wait: { color: "warning", tone: "warning", label: "statusRetryWait" },
  not_scheduled: { color: "default", tone: "muted", label: "statusNotScheduled" },
};

const STATUS_PRIORITY = {
  failed: 0,
  interrupted: 1,
  retry_wait: 2,
  not_scheduled: 3,
  partial: 4,
  running: 5,
  success: 6,
};

const ATTENTION_STATUSES = ["failed", "interrupted", "retry_wait", "not_scheduled"];

const COVERAGE_LABELS = {
  full_detail: "coverageFullDetail",
  headers_only: "coverageHeaders",
  full_list: "coverageFullList",
  approved_rows_only: "coverageApproved",
  full_catalog: "coverageCatalog",
  changed_only: "coverageChanged",
  flow_mapped: "coverageFlowMapped",
  day_complete_historical_pending: "coverageDayOnly",
  partial: "coveragePartial",
  provisional: "coverageProvisional",
  list_only: "coverageListOnly",
  observed_only: "coverageObserved",
  initial_tabs_bounded: "coverageTabsBounded",
  unknown: "coverageUnknown",
};

const VERIFICATION_LABELS = {
  single_scan_and_capture: "verifySingleScan",
  export_total_and_capture: "verifyExportTotal",
  source_window_and_capture: "verifySourceWindow",
  source_total_and_unique_ids: "verifySourceTotal",
  profile_count_and_capture: "verifyProfileCount",
  changed_tab_status_audit: "verifyChangedTab",
  object_field_readback: "verifyObjectReadback",
  derived_from_product_capture: "verifyDerived",
  sync_objects_done_count: "verifyObjectsDone",
  source_flow_id_join_and_total: "verifyFlowJoin",
  changed_product_tab_objects: "verifyChangedProductTabs",
  detail_capture_and_profile_count: "verifyDetailProfile",
  reused_export_total_and_capture: "verifyReusedExport",
  single_scan_count: "verifySingleCount",
  provisional_single_scan: "verifyProvisionalScan",
};

const MODE_LABELS = {
  object: "modeObject",
  final: "modeFinal",
  reconstructed: "modeReconstructed",
  incremental: "modeIncremental",
  snapshot: "modeSnapshot",
};

const GROUPS = [
  { key: "live", labelKey: "groupLive", test: (stream) => stream.startsWith("live:") },
  { key: "retail", labelKey: "groupRetail", test: (stream) => stream.startsWith("retail-") },
  { key: "purchase", labelKey: "groupPurchase", test: (stream) => stream.startsWith("purchase-") },
  {
    key: "product",
    labelKey: "groupProduct",
    test: (stream) => stream.startsWith("product-") || stream.startsWith("item_"),
  },
  { key: "master", labelKey: "groupMaster", test: () => true },
];

const shown = (value) => (value == null || value === "" ? "—" : String(value));

const toMillis = (value) => {
  if (value == null || value === "") return null;
  if (typeof value === "number") return value < 1e12 ? value * 1000 : value;
  const text = String(value).trim();
  if (!text) return null;
  if (/^\d{10,}$/.test(text)) {
    const num = Number(text);
    return num < 1e12 ? num * 1000 : num;
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return Date.parse(`${text}T00:00:00+08:00`);
  const iso = text.includes("T") ? text : text.replace(" ", "T");
  const withZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : `${iso}+08:00`;
  const millis = Date.parse(withZone);
  return Number.isFinite(millis) ? millis : null;
};

const formatTime = (value, pattern = "MM-DD HH:mm") => {
  const millis = toMillis(value);
  return millis == null ? "—" : dayjs(millis).format(pattern);
};

const formatNumber = (value) => {
  if (value == null || value === "") return "—";
  const num = Number(value);
  return Number.isFinite(num) ? num.toLocaleString() : String(value);
};

const formatRelative = (millis, nowMillis, t) => {
  if (millis == null) return "—";
  const minutes = Math.max(0, Math.floor((nowMillis - millis) / 60000));
  if (minutes < 1) return t("justNow");
  if (minutes < 60) return `${minutes} ${t("minutesAgo")}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${t("hoursAgo")}`;
  return `${Math.floor(hours / 24)} ${t("daysAgo")}`;
};

const formatDuration = (from, to, t) => {
  const start = toMillis(from);
  const end = toMillis(to);
  if (start == null || end == null || end < start) return "—";
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) return `${seconds} ${t("seconds")}`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} ${t("minutes")}`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} ${t("hours")} ${rest} ${t("minutes")}` : `${hours} ${t("hours")}`;
};

const formatLag = (minutes, t) => {
  if (minutes == null) return "—";
  if (minutes < 60) return `${minutes} ${t("minutes")}`;
  const hours = minutes / 60;
  if (hours < 24) {
    return `${hours >= 10 ? Math.round(hours) : hours.toFixed(1)} ${t("hours")}`;
  }
  const days = hours / 24;
  return `${days >= 10 ? Math.round(days) : days.toFixed(1)} ${t("daysUnit")}`;
};

const firstLine = (value) => {
  const text = String(value || "").trim();
  if (!text) return "";
  return text.split("\n").find((line) => line.trim()) || text;
};

const toneOf = (status) => (STATUS_META[status] ? STATUS_META[status].tone : "muted");

const buildEventWhere = (filter) => {
  if (filter === "success") return { status: { $eq: "success" } };
  if (filter === "failed") return { status: { $in: ["failed", "interrupted"] } };
  if (filter === "running") return { status: { $eq: "running" } };
  return {};
};

const isThemeColor = (value) => /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(value || "");

const themeConfig = (appTheme) => ({
  cssVar: { key: "sync-status-page" },
  algorithm: appTheme?.mode === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
  ...(isThemeColor(appTheme?.skinColor) ? { token: { colorPrimary: appTheme.skinColor } } : {}),
});

const App = () => {
  const client = useSdkClient();
  const $i18n = useI18n();
  const appTheme = useAppTheme();
  $i18n.addLocale(locales);
  const t = (key) => $i18n.t(key);

  const [statuses, setStatuses] = useState([]);
  const [events, setEvents] = useState([]);
  const [eventPage, setEventPage] = useState(1);
  const [eventTotal, setEventTotal] = useState(0);
  const [eventFilter, setEventFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [groupFilter, setGroupFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [nowMillis, setNowMillis] = useState(Date.now());
  const [lastLoadedAt, setLastLoadedAt] = useState(null);
  const [selected, setSelected] = useState(null);

  const load = useCallback(
    async (page = 1, filter = "all") => {
      setLoading(true);
      setError("");
      try {
        const [statusResult, eventResult] = await Promise.all([
          client.models[STATUS_MODEL].filter({
            where: {},
            currentPage: 1,
            pageSize: 200,
            orderBy: [{ updated_at: "DESC" }],
            select: [
              "id",
              "stream",
              "stream_name",
              "last_attempt_status",
              "last_attempt_at",
              "last_success_at",
              "covered_through",
              "last_source_rows",
              "last_saved_rows",
              "verification_method",
              "coverage_level",
              "coverage_note",
              "last_error",
              "last_event_id",
              "updated_at",
            ],
          }),
          client.models[EVENT_MODEL].filter({
            where: buildEventWhere(filter),
            currentPage: page,
            pageSize: PAGE_SIZE,
            orderBy: [{ started_at: "DESC" }, { id: "DESC" }],
            select: [
              "id",
              "stream",
              "mode",
              "source_from",
              "source_to",
              "status",
              "source_rows",
              "saved_rows",
              "coverage_level",
              "coverage_note",
              "started_at",
              "finished_at",
              "error_message",
            ],
          }),
        ]);
        setStatuses(Array.isArray(statusResult?.tableData) ? statusResult.tableData : []);
        setEvents(Array.isArray(eventResult?.tableData) ? eventResult.tableData : []);
        setEventTotal(Number(eventResult?.paging?.totalCount || 0));
        setEventPage(page);
        setLastLoadedAt(Date.now());
        setNowMillis(Date.now());
      } catch (err) {
        setError(err?.message || String(err));
        console.error("sync status load failed", err);
      } finally {
        setLoading(false);
      }
    },
    [client],
  );

  useEffect(() => {
    void load(1, eventFilter);
  }, [load, eventFilter]);

  useEffect(() => {
    const timer = setInterval(() => setNowMillis(Date.now()), 60 * 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const timer = setInterval(() => {
      void load(eventPage, eventFilter);
    }, AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [load, eventPage, eventFilter]);

  const streamNameMap = useMemo(
    () => Object.fromEntries(statuses.map((row) => [row.stream, row.stream_name])),
    [statuses],
  );

  const statusMeta = (value) => STATUS_META[value] || { color: "default", tone: "muted", label: "" };
  const renderStatusTag = (value) => {
    const meta = statusMeta(value);
    return <Tag color={meta.color}>{meta.label ? t(meta.label) : shown(value)}</Tag>;
  };
  const coverageLabel = (value) => (COVERAGE_LABELS[value] ? t(COVERAGE_LABELS[value]) : shown(value));
  const verificationLabel = (value) =>
    VERIFICATION_LABELS[value] ? t(VERIFICATION_LABELS[value]) : shown(value);
  const modeLabel = (value) => (MODE_LABELS[value] ? t(MODE_LABELS[value]) : shown(value));

  const streamGroupKey = useCallback((stream) => {
    const match = GROUPS.find((group) => group.test(String(stream || "")));
    return match ? match.key : "master";
  }, []);

  const totalStreams = statuses.length;
  const normalStreams = statuses.filter((row) => row.last_attempt_status === "success").length;
  const attentionStreams = statuses.filter((row) =>
    ATTENTION_STATUSES.includes(row.last_attempt_status),
  ).length;
  const successRate = totalStreams ? Math.round((normalStreams / totalStreams) * 100) : 0;
  const recentStreams = statuses.filter((row) => {
    const millis = toMillis(row.last_success_at);
    return millis != null && nowMillis - millis <= DAY_MS;
  }).length;
  const latestSuccessMs = statuses.reduce((latest, row) => {
    const millis = toMillis(row.last_success_at);
    return millis != null && (latest == null || millis > latest) ? millis : latest;
  }, null);

  const visibleStatuses = statuses
    .filter((row) => {
      const keyword = search.trim().toLowerCase();
      const matchesKeyword =
        !keyword ||
        [row.stream, row.stream_name].some((value) =>
          String(value || "")
            .toLowerCase()
            .includes(keyword),
        );
      const matchesStatus =
        statusFilter === "all" ||
        (statusFilter === "normal"
          ? row.last_attempt_status === "success"
          : row.last_attempt_status !== "success");
      const matchesGroup =
        groupFilter === "all" || streamGroupKey(row.stream) === groupFilter;
      return matchesKeyword && matchesStatus && matchesGroup;
    })
    .sort((a, b) => {
      const priority =
        (STATUS_PRIORITY[a.last_attempt_status] ?? 9) -
        (STATUS_PRIORITY[b.last_attempt_status] ?? 9);
      if (priority !== 0) return priority;
      return (
        (toMillis(b.last_attempt_at) || 0) - (toMillis(a.last_attempt_at) || 0)
      );
    });

  const attentionRows = statuses
    .filter((row) => ATTENTION_STATUSES.includes(row.last_attempt_status))
    .sort(
      (a, b) =>
        (toMillis(b.last_attempt_at) || 0) - (toMillis(a.last_attempt_at) || 0),
    );

  const resetFilters = () => {
    setSearch("");
    setStatusFilter("all");
    setGroupFilter("all");
  };

  const kpis = [
    {
      key: "total",
      tone: "primary",
      icon: <DatabaseOutlined />,
      label: t("totalStreams"),
      value: totalStreams,
      unit: t("streamUnit"),
      hint: t("totalStreamsHint"),
    },
    {
      key: "normal",
      tone: "success",
      icon: null,
      label: t("normalStreams"),
      value: normalStreams,
      unit: `/ ${totalStreams}`,
      hint: `${t("normalStreamsHint")} · ${successRate}%`,
    },
    {
      key: "attention",
      tone: attentionStreams ? "warning" : "success",
      icon: null,
      label: t("attentionStreams"),
      value: attentionStreams,
      unit: t("streamUnit"),
      hint: attentionStreams ? t("attentionStreamsHint") : t("attentionAllClear"),
    },
    {
      key: "recent",
      tone: "primary",
      icon: null,
      label: t("recentlyUpdated"),
      value: recentStreams,
      unit: t("streamUnit"),
      hint: latestSuccessMs
        ? `${t("recentlyUpdatedHint")} · ${formatRelative(latestSuccessMs, nowMillis, t)}`
        : t("noRecentSuccess"),
    },
  ];

  const lagOf = (row) => {
    const millis = toMillis(row.last_success_at);
    if (millis == null) return { minutes: null, tone: "muted" };
    const minutes = Math.max(0, Math.floor((nowMillis - millis) / 60000));
    const tone = minutes > 90 ? "danger" : minutes > 45 ? "warning" : "success";
    return { minutes, tone };
  };

  const streamColumns = [
    {
      title: t("tableStream"),
      key: "stream",
      render: (_, row) => (
        <div className="sync-stream-cell">
          <span className="sync-stream-name">{shown(row.stream_name || row.stream)}</span>
          <span className="sync-stream-code">{shown(row.stream)}</span>
        </div>
      ),
    },
    {
      title: t("tableStatus"),
      dataIndex: "last_attempt_status",
      key: "status",
      width: 104,
      render: renderStatusTag,
    },
    {
      title: t("tableLastSuccess"),
      key: "last_success",
      width: 150,
      render: (_, row) => (
        <div className="sync-time-cell">
          <span>{formatTime(row.last_success_at, "MM-DD HH:mm")}</span>
          <span className="sync-sub">{formatRelative(toMillis(row.last_success_at), nowMillis, t)}</span>
        </div>
      ),
    },
    {
      title: t("tableLag"),
      key: "lag",
      width: 120,
      render: (_, row) => {
        const lag = lagOf(row);
        return (
          <span className={`sync-lag is-${lag.tone}`}>
            {formatLag(lag.minutes, t)}
          </span>
        );
      },
    },
    {
      title: t("tableRows"),
      key: "rows",
      width: 170,
      render: (_, row) => {
        const saved = Number(row.last_saved_rows);
        const source = Number(row.last_source_rows);
        const percent =
          Number.isFinite(saved) && Number.isFinite(source) && source > 0
            ? Math.min(100, Math.round((saved / source) * 100))
            : 0;
        return (
          <div className="sync-ratio">
            <div className="sync-ratio-bar">
              <span style={{ width: `${percent}%` }} />
            </div>
            <span className="sync-ratio-text">
              {formatNumber(row.last_saved_rows)} / {formatNumber(row.last_source_rows)}
            </span>
          </div>
        );
      },
    },
    {
      title: t("tableCoverage"),
      dataIndex: "coverage_level",
      key: "coverage_level",
      width: 130,
      render: coverageLabel,
    },
    {
      title: t("tableUpdated"),
      key: "updated",
      width: 120,
      render: (_, row) => <span className="sync-muted">{formatTime(row.updated_at, "MM-DD HH:mm")}</span>,
    },
    {
      title: "",
      key: "action",
      width: 88,
      align: "right",
      render: (_, row) => (
        <Button type="link" size="small" onClick={() => setSelected(row)}>
          {t("tableAction")} <RightOutlined />
        </Button>
      ),
    },
  ];

  const eventColumns = [
    {
      title: t("startedAt"),
      dataIndex: "started_at",
      key: "started_at",
      width: 150,
      render: (value) => (
        <div className="sync-time-cell">
          <span>{formatTime(value, "MM-DD HH:mm:ss")}</span>
          <span className="sync-sub">{formatRelative(toMillis(value), nowMillis, t)}</span>
        </div>
      ),
    },
    {
      title: t("dataCategory"),
      dataIndex: "stream",
      key: "stream",
      width: 210,
      render: (value) => (
        <div className="sync-stream-cell">
          <span className="sync-stream-name">{shown(streamNameMap[value] || value)}</span>
          <span className="sync-stream-code">{shown(value)}</span>
        </div>
      ),
    },
    {
      title: t("status"),
      dataIndex: "status",
      key: "status",
      width: 92,
      render: renderStatusTag,
    },
    {
      title: t("sourceToSaved"),
      key: "rows",
      width: 150,
      render: (_, row) => (
        <span className="sync-rows">
          {formatNumber(row.source_rows)}
          <ArrowRightOutlined />
          {formatNumber(row.saved_rows)}
        </span>
      ),
    },
    {
      title: t("duration"),
      key: "duration",
      width: 110,
      render: (_, row) => formatDuration(row.started_at, row.finished_at, t),
    },
    {
      title: t("problem"),
      key: "problem",
      render: (_, row) => {
        const message = row.error_message || row.coverage_note;
        if (!message) return <span className="sync-muted">—</span>;
        return (
          <Tooltip title={<span className="sync-tooltip-text">{message}</span>} placement="topLeft">
            <span className={`sync-problem ${row.error_message ? "is-error" : ""}`}>
              {firstLine(message)}
            </span>
          </Tooltip>
        );
      },
    },
  ];

  const renderEventExpanded = (row) => (
    <div className="sync-event-expanded">
      <div className="sync-event-expanded-grid">
        <div>
          <span>{t("mode")}</span>
          <strong>{modeLabel(row.mode)}</strong>
        </div>
        <div>
          <span>{t("coverage")}</span>
          <strong>{coverageLabel(row.coverage_level)}</strong>
        </div>
        <div>
          <span>{t("finishedAt")}</span>
          <strong>{formatTime(row.finished_at, "YYYY-MM-DD HH:mm:ss")}</strong>
        </div>
        <div>
          <span>{t("sourceWindow")}</span>
          <strong>
            {row.source_from || row.source_to
              ? `${formatTime(row.source_from, "YYYY-MM-DD")} ~ ${formatTime(row.source_to, "YYYY-MM-DD")}`
              : "—"}
          </strong>
        </div>
      </div>
      {row.coverage_note ? (
        <p className="sync-event-note">
          <strong>{t("coverageNote")}</strong>
          <br />
          {row.coverage_note}
        </p>
      ) : null}
      {row.error_message ? (
        <pre className="sync-error-pre">
          <strong>{t("error")}</strong>
          {"\n"}
          {row.error_message}
        </pre>
      ) : null}
    </div>
  );

  const updatedLabel = lastLoadedAt ? dayjs(lastLoadedAt).format("HH:mm:ss") : t("notLoaded");
  const groupOptions = [
    { label: t("groupAll"), value: "all" },
    ...GROUPS.map((group) => ({ label: t(group.labelKey), value: group.key })),
  ];

  return (
    <ConfigProvider theme={themeConfig(appTheme)}>
      <main className="sync-page">
        <div className="sync-shell">
          <header className="sync-header">
            <div className="sync-header__main">
              <h1 className="sync-header__title">{t("pageTitle")}</h1>
              <p className="sync-header__subtitle">
                {t("subtitle")}
                <span className="sync-header__dot"> · </span>
                {t("updatedAt")} <strong>{updatedLabel}</strong>
              </p>
            </div>
            <div className="sync-header__actions">
              <span className="sync-auto">
                <span className="sync-auto__dot" />
                {t("autoRefresh")}
              </span>
              <Button
                type="primary"
                icon={<ReloadOutlined />}
                loading={loading}
                onClick={() => void load(eventPage, eventFilter)}
              >
                {t("refresh")}
              </Button>
            </div>
          </header>

          <section className="sync-summary">
            {kpis.map((kpi) => (
              <div key={kpi.key} className={`sync-stat is-${kpi.tone}`}>
                <div className="sync-stat__label">
                  {kpi.icon ? <span className="sync-stat__icon">{kpi.icon}</span> : null}
                  {kpi.label}
                </div>
                <div className="sync-stat__value">
                  {kpi.value}
                  <span className="sync-stat__unit">{kpi.unit}</span>
                </div>
                <div className="sync-stat__hint">{kpi.hint}</div>
              </div>
            ))}
          </section>

          {attentionRows.length ? (
            <section className="sync-attention">
              <div className="sync-attention__head">
                <span className="sync-attention__icon">
                  <WarningOutlined />
                </span>
                <div>
                  <strong>{t("attentionTitle")}</strong>
                  <span className="sync-attention__hint">{t("attentionHint")}</span>
                </div>
              </div>
              <ul className="sync-attention__list">
                {attentionRows.slice(0, 3).map((row) => (
                  <li key={row.id}>
                    <button type="button" onClick={() => setSelected(row)}>
                      <span className={`sync-dot is-${toneOf(row.last_attempt_status)}`} />
                      <span className="sync-attention__name">
                        {shown(row.stream_name || row.stream)}
                      </span>
                      <span className="sync-attention__problem">
                        {firstLine(row.last_error) ||
                          t(statusMeta(row.last_attempt_status).label || "statusFailed")}
                      </span>
                      <span className="sync-attention__time">
                        {formatRelative(toMillis(row.last_attempt_at), nowMillis, t)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {error ? (
            <Alert
              type="error"
              showIcon
              className="sync-error-alert"
              message={t("loadFailed")}
              description={error}
              action={
                <Button size="small" onClick={() => void load(eventPage, eventFilter)}>
                  {t("retry")}
                </Button>
              }
            />
          ) : null}

          <section className="sync-panel">
            <Tabs
              className="sync-tabs"
              items={[
                {
                  key: "status",
                  label: (
                    <span className="sync-tab-label">
                      {t("statusTab")}
                      <span className="sync-tab-count">{totalStreams}</span>
                    </span>
                  ),
                  children: (
                    <div className="sync-board">
                      <div className="sync-toolbar">
                        <Input
                          allowClear
                          className="sync-search"
                          prefix={<SearchOutlined />}
                          placeholder={t("searchPlaceholder")}
                          value={search}
                          onChange={(event) => setSearch(event.target.value)}
                        />
                        <Segmented
                          value={statusFilter}
                          onChange={setStatusFilter}
                          options={[
                            { label: t("filterAll"), value: "all" },
                            { label: t("filterNormal"), value: "normal" },
                            { label: t("filterAttention"), value: "attention" },
                          ]}
                        />
                        <Select
                          className="sync-group-select"
                          value={groupFilter}
                          onChange={setGroupFilter}
                          options={groupOptions}
                        />
                      </div>

                      <Table
                        rowKey="id"
                        size="middle"
                        loading={loading}
                        dataSource={visibleStatuses}
                        columns={streamColumns}
                        pagination={false}
                        scroll={{ x: 1120 }}
                        rowClassName={(row) =>
                          ATTENTION_STATUSES.includes(row.last_attempt_status) ? "sync-row-attention" : ""
                        }
                        locale={{
                          emptyText: (
                            <div className="sync-empty">
                              <Empty
                                image={Empty.PRESENTED_IMAGE_SIMPLE}
                                description={t("noMatch")}
                              />
                              {search || statusFilter !== "all" || groupFilter !== "all" ? (
                                <Button size="small" onClick={resetFilters}>
                                  {t("clearFilters")}
                                </Button>
                              ) : null}
                            </div>
                          ),
                        }}
                      />
                    </div>
                  ),
                },
                {
                  key: "events",
                  label: (
                    <span className="sync-tab-label">
                      {t("eventsTab")}
                      <span className="sync-tab-count">{eventTotal}</span>
                    </span>
                  ),
                  children: (
                    <div className="sync-board">
                      <div className="sync-toolbar">
                        <Segmented
                          value={eventFilter}
                          onChange={setEventFilter}
                          options={[
                            { label: t("filterAll"), value: "all" },
                            { label: t("filterSuccess"), value: "success" },
                            { label: t("filterFailed"), value: "failed" },
                            { label: t("filterRunning"), value: "running" },
                          ]}
                        />
                        <span className="sync-toolbar__meta">
                          <ClockCircleOutlined /> {t("autoRefresh")}
                        </span>
                      </div>
                      <Table
                        rowKey="id"
                        size="middle"
                        loading={loading}
                        dataSource={events}
                        columns={eventColumns}
                        tableLayout="fixed"
                        scroll={{ x: 1000 }}
                        rowClassName={(row) => (row.status === "failed" ? "sync-row-attention" : "")}
                        expandable={{
                          expandedRowRender: renderEventExpanded,
                          rowExpandable: (row) => Boolean(row.error_message || row.coverage_note),
                        }}
                        pagination={{
                          current: eventPage,
                          pageSize: PAGE_SIZE,
                          total: eventTotal,
                          showSizeChanger: false,
                          showTotal: (total) => `${t("totalRecords")} ${total} ${t("records")}`,
                          onChange: (page) => void load(page, eventFilter),
                        }}
                        locale={{
                          emptyText: (
                            <Empty
                              image={Empty.PRESENTED_IMAGE_SIMPLE}
                              description={t("noData")}
                            />
                          ),
                        }}
                      />
                    </div>
                  ),
                },
              ]}
            />
          </section>

          <footer className="sync-footer">
            <InfoCircleOutlined />
            <span>{t("freshnessNotice")}</span>
          </footer>
        </div>

        <Drawer
          open={Boolean(selected)}
          onClose={() => setSelected(null)}
          width="min(540px, 94vw)"
          title={t("drawerTitle")}
          className="sync-drawer"
        >
          {selected ? (
            <div className="sync-drawer-body">
              <div className="sync-drawer-head">
                <span className={`sync-dot is-${toneOf(selected.last_attempt_status)}`} />
                <div className="sync-drawer-title">
                  <strong>{shown(selected.stream_name || selected.stream)}</strong>
                  <span className="sync-stream-code">{shown(selected.stream)}</span>
                </div>
                {renderStatusTag(selected.last_attempt_status)}
              </div>

              <div className="sync-drawer-metrics">
                <div>
                  <span>{t("syncAge")}</span>
                  <strong>{formatRelative(toMillis(selected.last_success_at), nowMillis, t)}</strong>
                </div>
                <div>
                  <span>{t("rowsSaved")}</span>
                  <strong>
                    {formatNumber(selected.last_saved_rows)} / {formatNumber(selected.last_source_rows)}
                  </strong>
                </div>
                <div>
                  <span>{t("coverage")}</span>
                  <strong>{coverageLabel(selected.coverage_level)}</strong>
                </div>
              </div>

              <Descriptions column={1} size="small" className="sync-desc">
                <Descriptions.Item label={t("lastAttempt")}>
                  {formatTime(selected.last_attempt_at, "YYYY-MM-DD HH:mm:ss")}
                </Descriptions.Item>
                <Descriptions.Item label={t("lastSuccess")}>
                  {formatTime(selected.last_success_at, "YYYY-MM-DD HH:mm:ss")}
                </Descriptions.Item>
                <Descriptions.Item label={t("coveredThrough")}>
                  {formatTime(selected.covered_through, "YYYY-MM-DD")}
                </Descriptions.Item>
                <Descriptions.Item label={t("verification")}>
                  {verificationLabel(selected.verification_method)}
                </Descriptions.Item>
                <Descriptions.Item label={t("updatedTime")}>
                  {formatTime(selected.updated_at, "YYYY-MM-DD HH:mm:ss")}
                </Descriptions.Item>
                <Descriptions.Item label={t("eventId")}>
                  {shown(selected.last_event_id)}
                </Descriptions.Item>
              </Descriptions>

              <section className="sync-drawer-section">
                <div className="sync-drawer-section-title">{t("coverageNote")}</div>
                <p>{selected.coverage_note || t("noData")}</p>
              </section>

              {selected.last_error ? (
                <section className="sync-drawer-section">
                  <div className="sync-drawer-section-title">
                    <ExclamationCircleFilled /> {t("lastError")}
                  </div>
                  <pre className="sync-error-pre">{selected.last_error}</pre>
                </section>
              ) : null}
            </div>
          ) : null}
        </Drawer>
      </main>
    </ConfigProvider>
  );
};

App.displayName = "KemaiSyncStatusPage";

export default App;
