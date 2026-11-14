import React from "react";
import { Tooltip } from "antd";
import { formatNumber, formatPercent, toNumber } from "../lib/format";

const statusOf = (value, warning) => {
  if (value === null || value === undefined) return "muted";
  return warning ? "warn" : "ok";
};

const CoverageStrip = ({ coverage, warnings, $t, onOpenMetric }) => {
  if (!coverage) return null;
  const payment = toNumber(coverage.payment_fact_count);
  const items = [
    {
      key: "cost",
      label: $t("coverage.cost"),
      value: formatPercent(coverage.cost_coverage_rate),
      hint: $t("coverage.costHint"),
      status: statusOf(coverage.cost_coverage_rate, (coverage.cost_coverage_rate || 0) < 1),
      metric: "cost_coverage_rate",
    },
    {
      key: "inventory",
      label: $t("coverage.inventory"),
      value: formatPercent(coverage.snapshot_sales_coverage),
      hint: $t("coverage.inventoryHint"),
      status: statusOf(coverage.snapshot_sales_coverage, (coverage.snapshot_sales_coverage || 0) < 1),
      metric: "snapshot_sales_coverage",
    },
    {
      key: "promotion",
      label: $t("coverage.promotion"),
      value: formatPercent(coverage.promotion_link_rate),
      hint: $t("coverage.promotionHint"),
      status: statusOf(coverage.promotion_link_rate, (coverage.promotion_link_rate || 0) < 1),
      metric: "promotion_link_rate",
    },
    {
      key: "payment",
      label: $t("coverage.payment"),
      value: payment === null ? "—" : formatNumber(payment, 0),
      hint: $t("coverage.paymentHint"),
      status: payment !== null && payment >= 100 ? "ok" : "warn",
      metric: null,
    },
  ];
  const warningLabels = {
    COVERAGE_UNAVAILABLE: "coverage.warning.unavailable",
    COST_COVERAGE_PARTIAL: "coverage.warning.cost",
    MEMBER_IDENTIFICATION_PARTIAL: "coverage.warning.member",
    INVENTORY_SNAPSHOT_PARTIAL: "coverage.warning.inventory",
    INVENTORY_SNAPSHOT_STALE: "coverage.warning.stale",
    PROMOTION_LINK_PARTIAL: "coverage.warning.promotion",
    PAYMENT_FACTS_INSUFFICIENT: "coverage.warning.payment",
  };

  return (
    <section className="metric-coverage" aria-label={$t("coverage.title")}>
      <div className="metric-coverage__head">
        <span className="metric-coverage__title">{$t("coverage.title")}</span>
        <span className="metric-coverage__note">{$t("coverage.note")}</span>
      </div>
      <div className="metric-coverage__items">
        {items.map((item) => {
          const body = (
            <>
              <span className={`metric-coverage__dot is-${item.status}`} aria-hidden="true" />
              <span className="metric-coverage__label">{item.label}</span>
              <strong className="metric-coverage__value">{item.value}</strong>
            </>
          );
          return (
            <Tooltip key={item.key} title={`${item.hint} ${$t("coverage.clickHint")}`}>
              {item.metric ? (
                <button
                  type="button"
                  className="metric-coverage__item"
                  onClick={() => onOpenMetric(item.metric)}
                >
                  {body}
                </button>
              ) : (
                <div className="metric-coverage__item is-static">{body}</div>
              )}
            </Tooltip>
          );
        })}
      </div>
      {warnings?.length ? (
        <div className="metric-coverage__warnings">
          {warnings.map((warning) => (
            <span key={warning} className="metric-coverage__warning">
              {$t(warningLabels[warning] || "coverage.warning.other")}
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
};

export default CoverageStrip;
