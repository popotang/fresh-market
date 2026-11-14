export const toNumber = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

export const isNil = (value) => value === null || value === undefined;

export const formatCurrency = (value, { compact = true, digits = 2 } = {}) => {
  const amount = toNumber(value);
  if (amount === null) return "—";
  const absolute = Math.abs(amount);
  if (compact && absolute >= 100000000) return `¥${(amount / 100000000).toFixed(2)}亿`;
  if (compact && absolute >= 10000) return `¥${(amount / 10000).toFixed(1)}万`;
  return `¥${amount.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
};

export const formatNumber = (value, digits = 0) => {
  const amount = toNumber(value);
  if (amount === null) return "—";
  return amount.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
};

export const formatPercent = (value, digits = 1) => {
  const ratio = toNumber(value);
  if (ratio === null) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
};

export const formatMetricValue = (metric, value) => {
  if (isNil(value)) return "—";
  const unit = metric?.unit || "";
  if (unit === "ratio") return formatPercent(value);
  if (unit === "CNY" || unit === "CNY/order" || unit === "CNY/quantity") {
    return formatCurrency(value);
  }
  if (unit === "quantity" || unit === "quantity/order") return formatNumber(value, 1);
  if (unit === "day") return `${formatNumber(value, 1)} 天`;
  if (unit === "hour") return `${formatNumber(value, 0)} 小时`;
  return formatNumber(value, 0);
};

export const unitLabel = (unit) => {
  const labels = {
    CNY: "元",
    "CNY/order": "元/单",
    "CNY/quantity": "元/件",
    ratio: "%",
    order: "单",
    quantity: "件",
    "quantity/order": "件/单",
    sku: "个 SKU",
    day: "天",
    hour: "小时",
  };
  return labels[unit] || unit || "";
};
