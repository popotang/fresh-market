import React, { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, ConfigProvider, Empty, Table, Tabs, Tag, Typography, theme as antdTheme } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useAppTheme, useI18n, useLocation, useNavigate, useSdkClient } from "@/context/app-context";
import { locales } from "../locales";
import "./index.css";

const STATUS_MODEL = "dataset_eac713d6c72646c9b2b3a2ee0f8e31eb";
const EVENT_MODEL = "dataset_b864ca67ee41418c9a6c3a189b726be6";
const PAGE_SIZE = 20;

const shown = (value) => value == null || value === "" ? "—" : String(value);
const shownTime = (value) => value == null || value === "" ? "—" : String(value).replace("T", " ").slice(0, 19);
const lagMinutes = (value, nowMillis) => {
  if (!value) return null;
  const raw = String(value).replace(" ", "T");
  const withZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw) ? raw : `${raw}+08:00`;
  const millis = Date.parse(withZone);
  return Number.isFinite(millis) ? Math.max(0, Math.floor((nowMillis - millis) / 60000)) : null;
};

const App = () => {
  const client = useSdkClient();
  const $i18n = useI18n();
  useNavigate();
  useLocation();
  const appTheme = useAppTheme();
  $i18n.addLocale(locales);

  const [statuses, setStatuses] = useState([]);
  const [events, setEvents] = useState([]);
  const [eventPage, setEventPage] = useState(1);
  const [eventTotal, setEventTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [nowMillis, setNowMillis] = useState(Date.now());

  const load = useCallback(async (page) => {
    setLoading(true);
    setError("");
    try {
      const [statusResult, eventResult] = await Promise.all([
        client.models[STATUS_MODEL].filter({
          where: {}, currentPage: 1, pageSize: 100,
          select: ["id", "stream", "stream_name", "last_attempt_status", "last_attempt_at", "last_success_at", "covered_through", "last_source_rows", "last_saved_rows", "coverage_level", "coverage_note", "last_error"],
        }),
        client.models[EVENT_MODEL].filter({
          where: {}, currentPage: page, pageSize: PAGE_SIZE,
          orderBy: [{ started_at: "DESC" }, { id: "DESC" }],
          select: ["id", "stream", "mode", "source_from", "source_to", "status", "source_rows", "saved_rows", "coverage_level", "coverage_note", "started_at", "finished_at", "error_message"],
        }),
      ]);
      setStatuses(Array.isArray(statusResult?.tableData) ? statusResult.tableData : []);
      setEvents(Array.isArray(eventResult?.tableData) ? eventResult.tableData : []);
      setEventTotal(Number(eventResult?.paging?.totalCount || 0));
      setEventPage(page);
    } catch (err) {
      setError(err?.message || String(err));
      console.error("sync status load failed", err);
    } finally {
      setLoading(false);
    }
  }, [client]);

  useEffect(() => { void load(1); }, [load]);
  useEffect(() => {
    const timer = setInterval(() => setNowMillis(Date.now()), 60 * 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const timer = setInterval(() => { void load(eventPage); }, 5 * 60 * 1000);
    return () => clearInterval(timer);
  }, [load, eventPage]);

  const t = (key) => $i18n.t(key);
  const statusTag = (value) => {
    const colors = { success: "success", failed: "error", partial: "warning", running: "processing", interrupted: "warning", retry_wait: "warning", not_scheduled: "default" };
    const labels = { success: "statusSuccess", failed: "statusFailed", partial: "statusPartial", running: "statusRunning", interrupted: "statusInterrupted", retry_wait: "statusRetryWait", not_scheduled: "statusNotScheduled" };
    return <Tag color={colors[value] || "default"}>{labels[value] ? t(labels[value]) : shown(value)}</Tag>;
  };
  const coverage = (value) => {
    const labels = { full_detail: "coverageFullDetail", headers_only: "coverageHeaders", full_list: "coverageFullList", approved_rows_only: "coverageApproved", full_catalog: "coverageCatalog", changed_only: "coverageChanged", flow_mapped: "coverageFlowMapped", day_complete_historical_pending: "coverageDayOnly", partial: "coveragePartial", provisional: "coverageProvisional" };
    return labels[value] ? t(labels[value]) : shown(value);
  };

  const statusColumns = [
    { title: t("dataCategory"), dataIndex: "stream_name", key: "stream_name", fixed: "left", width: 170, render: (value, row) => shown(value || row.stream) },
    { title: t("coveredThrough"), dataIndex: "covered_through", key: "covered_through", width: 125, render: shownTime },
    { title: t("lastSuccess"), dataIndex: "last_success_at", key: "last_success_at", width: 175, render: shownTime },
    { title: t("liveLag"), key: "liveLag", width: 125, render: (_, row) => {
      if (!String(row.stream).startsWith("live:")) return "—";
      const minutes = lagMinutes(row.last_success_at, nowMillis);
      return minutes == null ? "—" : <Tag color={minutes > 90 ? "error" : minutes > 45 ? "warning" : "success"}>{minutes} {t("minutes")}</Tag>;
    } },
    { title: t("lastAttempt"), dataIndex: "last_attempt_at", key: "last_attempt_at", width: 175, render: shownTime },
    { title: t("attemptStatus"), dataIndex: "last_attempt_status", key: "last_attempt_status", width: 110, render: statusTag },
    { title: t("sourceRows"), dataIndex: "last_source_rows", key: "last_source_rows", width: 105, render: shown },
    { title: t("savedRows"), dataIndex: "last_saved_rows", key: "last_saved_rows", width: 105, render: shown },
    { title: t("coverage"), dataIndex: "coverage_level", key: "coverage_level", width: 120, render: coverage },
    { title: t("coverageNote"), dataIndex: "coverage_note", key: "coverage_note", width: 330, render: shown },
    { title: t("lastError"), dataIndex: "last_error", key: "last_error", width: 280, render: shown },
  ];
  const eventColumns = [
    { title: t("dataCategory"), dataIndex: "stream", key: "stream", width: 170, render: (value) => statuses.find((item) => item.stream === value)?.stream_name || shown(value) },
    { title: t("sourceWindow"), key: "window", width: 225, render: (_, row) => `${shown(row.source_from)} ~ ${shown(row.source_to)}` },
    { title: t("startedAt"), dataIndex: "started_at", key: "started_at", width: 175, render: shownTime },
    { title: t("finishedAt"), dataIndex: "finished_at", key: "finished_at", width: 175, render: shownTime },
    { title: t("attemptStatus"), dataIndex: "status", key: "status", width: 110, render: statusTag },
    { title: t("mode"), dataIndex: "mode", key: "mode", width: 120, render: (value) => value === "reconstructed" ? t("reconstructed") : shown(value) },
    { title: t("sourceRows"), dataIndex: "source_rows", key: "source_rows", width: 100, render: shown },
    { title: t("savedRows"), dataIndex: "saved_rows", key: "saved_rows", width: 100, render: shown },
    { title: t("coverage"), dataIndex: "coverage_level", key: "coverage_level", width: 120, render: coverage },
    { title: t("coverageNote"), dataIndex: "coverage_note", key: "coverage_note", width: 300, render: shown },
    { title: t("error"), dataIndex: "error_message", key: "error_message", width: 280, render: shown },
  ];

  return (
    <ConfigProvider theme={{ algorithm: appTheme?.mode === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm, cssVar: { key: "sync-status-page" }, ...(appTheme?.skinColor ? { token: { colorPrimary: appTheme.skinColor } } : {}) }}>
      <main className="sync-status-page">
        <header className="sync-status-header">
          <div><Typography.Title level={3}>{t("pageTitle")}</Typography.Title><Typography.Text type="secondary">{t("subtitle")}</Typography.Text></div>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load(eventPage)}>{t("refresh")}</Button>
        </header>
        <Alert type="info" showIcon message={t("freshnessNotice")} className="sync-status-notice" />
        {error && <Alert type="error" showIcon message={t("loadFailed")} description={error} action={<Button size="small" onClick={() => void load(eventPage)}>{t("retry")}</Button>} className="sync-status-notice" />}
        <Card>
          <Tabs items={[
            { key: "status", label: t("statusTab"), children: <Table rowKey="id" loading={loading} dataSource={statuses} columns={statusColumns} scroll={{ x: 1800 }} pagination={{ pageSize: 20 }} locale={{ emptyText: <Empty description={t("noData")} /> }} /> },
            { key: "events", label: t("eventsTab"), children: <Table rowKey="id" loading={loading} dataSource={events} columns={eventColumns} scroll={{ x: 1900 }} pagination={{ current: eventPage, pageSize: PAGE_SIZE, total: eventTotal, onChange: (page) => void load(page) }} locale={{ emptyText: <Empty description={t("noData")} /> }} /> },
          ]} />
        </Card>
      </main>
    </ConfigProvider>
  );
};

App.displayName = "KemaiSyncStatusPage";

export default App;
