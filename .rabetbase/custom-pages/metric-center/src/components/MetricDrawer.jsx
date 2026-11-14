import React from "react";
import { Drawer, Tag } from "antd";
import { formatMetricValue, unitLabel } from "../lib/format";

const groupLimits = {
  retail_performance: "drawer.limit.retail",
  inventory_health: "drawer.limit.inventory",
  purchase_performance: "drawer.limit.purchase",
  marketing_performance: "drawer.limit.marketing",
};

const MetricDrawer = ({ metric, value, group, $t, definitionVersion, onClose }) => (
  <Drawer
    open={Boolean(metric)}
    onClose={onClose}
    width={440}
    title={metric?.name || $t("drawer.title")}
    className="metric-drawer"
  >
    {metric ? (
      <div className="metric-drawer__body">
        <div className="metric-drawer__value">{formatMetricValue(metric, value)}</div>
        <div className="metric-drawer__unit">
          {unitLabel(metric.unit)} · {group?.name || metric.group}
        </div>

        <dl className="metric-drawer__facts">
          <div>
            <dt>{$t("drawer.code")}</dt>
            <dd>{metric.code}</dd>
          </div>
          <div>
            <dt>{$t("drawer.formula")}</dt>
            <dd className="metric-drawer__formula">{metric.formula}</dd>
          </div>
          <div>
            <dt>{$t("drawer.source")}</dt>
            <dd>{metric.source}</dd>
          </div>
          <div>
            <dt>{$t("drawer.dimensions")}</dt>
            <dd>{group?.dimensions?.join(" · ") || "—"}</dd>
          </div>
          <div>
            <dt>{$t("drawer.nullable")}</dt>
            <dd>{metric.nullable ? $t("drawer.nullableYes") : $t("drawer.nullableNo")}</dd>
          </div>
        </dl>

        <div className="metric-drawer__note">
          <span className="metric-drawer__note-label">{$t("drawer.limit")}</span>
          <p>{$t(groupLimits[metric.group] || "drawer.limit.general")}</p>
        </div>

        <div className="metric-drawer__footer">
          <Tag>{definitionVersion}</Tag>
          <span>{$t("drawer.versionHint")}</span>
        </div>
      </div>
    ) : null}
  </Drawer>
);

export default MetricDrawer;
