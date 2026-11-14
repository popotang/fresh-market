import React, { useCallback, useEffect, useState } from "react";
import {
  Button,
  ConfigProvider,
  Skeleton,
  Tag,
  theme as antdTheme,
} from "antd";
import {
  ArrowRightOutlined,
  BarChartOutlined,
  CompassOutlined,
  DatabaseOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import {
  useAppTheme,
  useI18n,
  useNavigate,
  useSdkClient,
} from "@/context/app-context";
import { locales } from "../locales";
import { BUSINESS_FLOW_IMAGE } from "../assets/business-flow";
import { formatNumber, formatPercent } from "../lib/format";
import "./index.css";

const ENDPOINT = "businessMetricGateway";
const PAGES = {
  metric: "e4333ec2deaa47dba436c87bd08ff2f9",
  owner: "e026e757db914413b440a2015353f8f2",
  store: "d87b6c0b2d93425aa1a83da6872058fb",
};

const getThemeConfig = (appTheme) => ({
  cssVar: { key: "project-home-theme" },
  algorithm: appTheme?.mode === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
  ...(appTheme?.skinColor ? { token: { colorPrimary: appTheme.skinColor } } : {}),
});

const boundaryMeta = [
  { key: "readonly", icon: SafetyCertificateOutlined },
  { key: "finance", icon: BarChartOutlined },
  { key: "stock", icon: DatabaseOutlined },
  { key: "coverage", icon: CompassOutlined },
];

const AppContent = () => {
  const client = useSdkClient();
  const $i18n = useI18n();
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState(null);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

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

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const results = await Promise.allSettled([
        client.bff.execute({ scriptName: ENDPOINT, params: { action: "catalog" } }),
        client.bff.execute({ scriptName: ENDPOINT, params: { action: "health" } }),
      ]);
      if (!alive) return;
      const [catalogResult, healthResult] = results;
      if (catalogResult.status === "fulfilled") setCatalog(catalogResult.value);
      if (healthResult.status === "fulfilled") setHealth(healthResult.value);
      setFailed(catalogResult.status === "rejected" && healthResult.status === "rejected");
      setLoading(false);
    };
    load();
    return () => {
      alive = false;
    };
  }, [client]);

  const go = useCallback(
    (page) => {
      if (!page) return;
      navigate(`/${PAGES[page]}`, { onePage: true });
    },
    [navigate],
  );

  const coverage = health?.coverage || {};
  const metricCount = catalog?.metrics?.length || 46;
  const latestDate = coverage.latest_biz_date || "—";
  const warningCount = health?.warnings?.length || 0;
  const statusLabel = failed ? $t("home.status.degraded") : $t("home.status.running");

  return (
    <div className="home-page">
      <header className="home-hero">
        <div className="home-hero__copy">
          <div className="home-brand">
            <span className="home-brand__mark" aria-hidden="true" />
            <span>{$t("home.brand")}</span>
            <span className="home-brand__divider" aria-hidden="true" />
            <span>{$t("home.kicker")}</span>
          </div>
          <h1>{$t("home.title")}</h1>
          <p className="home-hero__desc">{$t("home.desc")}</p>
          <div className="home-hero__actions">
            <Button type="primary" size="large" onClick={() => go("metric")}>
              {$t("home.action.metric")}
              <ArrowRightOutlined />
            </Button>
            <Button size="large" onClick={() => go("owner")}>
              {$t("home.action.owner")}
            </Button>
            <Button type="text" size="large" onClick={() => go("store")}>
              {$t("home.action.store")}
            </Button>
          </div>
        </div>

        <aside className="home-status" aria-live="polite">
          <div className="home-status__head">
            <span className={`home-status__dot ${failed ? "is-failed" : ""}`} aria-hidden="true" />
            <strong>{statusLabel}</strong>
          </div>
          <p className="home-status__note">{$t("home.status.note")}</p>
          {loading ? (
            <Skeleton active title={false} paragraph={{ rows: 4 }} />
          ) : (
            <dl className="home-status__facts">
              <div>
                <dt>{$t("home.status.latest")}</dt>
                <dd>{latestDate}</dd>
              </div>
              <div>
                <dt>{$t("home.status.metrics")}</dt>
                <dd>{formatNumber(metricCount)}</dd>
              </div>
              <div>
                <dt>{$t("home.status.cost")}</dt>
                <dd>{formatPercent(coverage.cost_coverage_rate)}</dd>
              </div>
              <div>
                <dt>{$t("home.status.inventory")}</dt>
                <dd>{formatPercent(coverage.snapshot_sales_coverage)}</dd>
              </div>
            </dl>
          )}
          <div className="home-status__foot">
            <Tag bordered={false}>{$t("home.status.warnings", { count: warningCount })}</Tag>
            <span>{$t("home.status.note")}</span>
          </div>
        </aside>
      </header>

      <main>
        <section className="home-diagram" aria-label={$t("home.diagram.alt")}>
          <img
            className="home-diagram__image"
            src={BUSINESS_FLOW_IMAGE}
            alt={$t("home.diagram.alt")}
          />
        </section>

        <section className="home-section home-architecture" aria-labelledby="architecture-title">
          <div className="home-section__head">
            <h2 id="architecture-title">{$t("home.architecture.title")}</h2>
            <p>{$t("home.architecture.desc")}</p>
          </div>
          <div className="architecture-grid">
            {[
              ["datasets", "47", "home.architecture.datasets", "home.architecture.datasetsHint"],
              ["relations", "59", "home.architecture.relations", "home.architecture.relationsHint"],
              ["metrics", formatNumber(metricCount), "home.architecture.metrics", "home.architecture.metricsHint"],
              ["pages", "3", "home.architecture.pages", "home.architecture.pagesHint"],
            ].map(([key, value, labelKey, hintKey]) => (
              <div className="architecture-stat" key={key}>
                <span className="architecture-stat__label">{$t(labelKey)}</span>
                <strong className="architecture-stat__value">{value}</strong>
                <span className="architecture-stat__hint">{$t(hintKey)}</span>
              </div>
            ))}
          </div>
          <p className="home-asof">
            {$t("home.architecture.asOf", { date: latestDate })}
          </p>
        </section>

        <section className="home-section home-boundary" aria-labelledby="boundary-title">
          <div className="home-section__head">
            <h2 id="boundary-title">{$t("home.boundary.title")}</h2>
            <p>{$t("home.boundary.desc")}</p>
          </div>
          <div className="boundary-grid">
            {boundaryMeta.map((item) => {
              const Icon = item.icon;
              return (
                <article className="boundary-item" key={item.key}>
                  <Icon className="boundary-item__icon" />
                  <div>
                    <h3>{$t(`boundary.${item.key}.title`)}</h3>
                    <p>{$t(`boundary.${item.key}.desc`)}</p>
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        <section className="home-section home-start" aria-labelledby="start-title">
          <div className="home-start__copy">
            <h2 id="start-title">{$t("home.start.title")}</h2>
            <p>{$t("home.start.desc")}</p>
          </div>
          <div className="home-start__actions">
            <Button type="primary" size="large" onClick={() => go("metric")}>
              {$t("home.start.metric")}
            </Button>
            <Button size="large" onClick={() => go("store")}>
              {$t("home.start.store")}
            </Button>
            <Button size="large" onClick={() => go("owner")}>
              {$t("home.start.owner")}
            </Button>
          </div>
        </section>
      </main>

      <footer className="home-footer">{$t("home.footer")}</footer>
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
