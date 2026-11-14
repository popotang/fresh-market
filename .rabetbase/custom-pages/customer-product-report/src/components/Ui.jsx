import React from "react";
import { Alert, Card, Table } from "antd";

export const Section = ({ id, number, title, intro, children }) => (
  <section className="report-section" id={id}>
    <div className="report-section__heading">
      <span className="report-section__number">{number}</span>
      <h2>{title}</h2>
    </div>
    {intro ? <p className="report-section__intro">{intro}</p> : null}
    {children}
  </section>
);

export const KpiGrid = ({ items, compact = false }) => (
  <div className={compact ? "report-kpis report-kpis--compact" : "report-kpis"}>
    {items.map((item) => (
      <Card className={`report-kpi report-kpi--${item.tone || "default"}`} key={item.label}>
        <div className="report-kpi__value">{item.value}</div>
        <div className="report-kpi__label">{item.label}</div>
        {item.hint ? <div className="report-kpi__hint">{item.hint}</div> : null}
      </Card>
    ))}
  </div>
);

export const Panel = ({ title, children, wide = false }) => (
  <Card className={wide ? "report-panel report-panel--wide" : "report-panel"}>
    {title ? <div className="report-panel__title">{title}</div> : null}
    {children}
  </Card>
);

export const Callout = ({ type = "info", title, children }) => (
  <Alert
    className="report-callout"
    type={type}
    showIcon={false}
    message={title ? <strong>{title}</strong> : null}
    description={children}
  />
);

export const ReportTable = ({ columns, data, rowKey, className = "" }) => (
  <Table
    className={`report-table ${className}`}
    columns={columns}
    dataSource={data}
    rowKey={rowKey}
    pagination={false}
    size="small"
    scroll={{ x: "max-content" }}
  />
);
