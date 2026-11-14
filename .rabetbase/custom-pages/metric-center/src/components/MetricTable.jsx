import React from "react";
import { Table, Tag } from "antd";
import { formatMetricValue, unitLabel } from "../lib/format";

const MetricTable = ({ metrics, values, groups, $t, onSelect }) => {
  const groupMap = new Map((groups || []).map((group) => [group.code, group]));
  const data = metrics.map((metric) => ({
    key: metric.code,
    metric,
    value: values?.[metric.code],
    group: groupMap.get(metric.group),
  }));

  const columns = [
    {
      title: $t("table.metric"),
      dataIndex: ["metric", "name"],
      key: "name",
      width: 190,
      render: (name, record) => (
        <div className="metric-table__name">
          <span>{name}</span>
          <small>{record.metric.code}</small>
        </div>
      ),
    },
    {
      title: $t("table.value"),
      dataIndex: "value",
      key: "value",
      width: 150,
      align: "right",
      render: (value, record) => (
        <span className={value === null || value === undefined ? "metric-table__empty" : "metric-table__value"}>
          {formatMetricValue(record.metric, value)}
        </span>
      ),
    },
    {
      title: $t("table.unit"),
      dataIndex: ["metric", "unit"],
      key: "unit",
      width: 96,
      render: (unit) => unitLabel(unit),
    },
    {
      title: $t("table.definition"),
      dataIndex: ["metric", "formula"],
      key: "formula",
      render: (formula) => <span className="metric-table__formula" title={formula}>{formula}</span>,
    },
    {
      title: $t("table.status"),
      key: "status",
      width: 112,
      render: (_, record) => {
        if (record.value === null || record.value === undefined) {
          return <Tag color="warning">{$t("table.pending")}</Tag>;
        }
        return <Tag color="success">{$t("table.calculated")}</Tag>;
      },
    },
  ];

  return (
    <Table
      className="metric-table"
      columns={columns}
      dataSource={data}
      pagination={false}
      size="small"
      scroll={{ x: 760 }}
      onRow={(record) => ({
        onClick: () => onSelect(record.metric),
        onKeyDown: (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onSelect(record.metric);
          }
        },
        tabIndex: 0,
        role: "button",
      })}
    />
  );
};

export default MetricTable;
