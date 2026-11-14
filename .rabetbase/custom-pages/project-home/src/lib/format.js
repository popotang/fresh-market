export const formatPercent = (value, digits = 1) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return `${(parsed * 100).toFixed(digits)}%`;
};

export const formatNumber = (value) => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return "—";
  return parsed.toLocaleString("zh-CN");
};
