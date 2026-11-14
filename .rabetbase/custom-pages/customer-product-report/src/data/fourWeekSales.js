const DAY_MS = 24 * 60 * 60 * 1000;
const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

const parseBusinessDate = (value) => {
  const match = DATE_PATTERN.exec(String(value || ""));
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null;
  }
  return date;
};

const formatBusinessDate = (date) => date.toISOString().slice(0, 10);

export const getFourWeekRange = (latestDate) => {
  const end = parseBusinessDate(latestDate);
  if (!end) return null;
  const start = new Date(end.getTime() - 27 * DAY_MS);
  if (start.getUTCFullYear() < 0) return null;
  return {
    startDate: formatBusinessDate(start),
    endDate: formatBusinessDate(end),
  };
};

export const buildFourWeekDailySeries = (latestDate, rows = []) => {
  const range = getFourWeekRange(latestDate);
  if (!range) return [];

  const amountByDate = new Map();
  (Array.isArray(rows) ? rows : []).forEach((row) => {
    const parsedDate = parseBusinessDate(row?.biz_date);
    if (!parsedDate) return;
    const date = formatBusinessDate(parsedDate);
    if (date < range.startDate || date > range.endDate) return;
    const rawAmount = row?.sales_amount;
    if (typeof rawAmount !== "number" && typeof rawAmount !== "string") return;
    if (typeof rawAmount === "string" && rawAmount.trim() === "") return;
    const amount = Number(rawAmount);
    if (!Number.isFinite(amount)) return;
    amountByDate.set(date, amount);
  });

  const start = parseBusinessDate(range.startDate);
  if (!start) return [];
  return Array.from({ length: 28 }, (_, index) => {
    const date = formatBusinessDate(new Date(start.getTime() + index * DAY_MS));
    return {
      date,
      label: date.slice(5),
      salesAmount: amountByDate.has(date) ? amountByDate.get(date) : null,
    };
  });
};

export const buildFourWeekTrend = (rows = []) => {
  const points = (Array.isArray(rows) ? rows : [])
    .map((row, index) => ({ index, value: Number(row?.salesAmount) }))
    .filter((point) => Number.isFinite(point.value));
  if (points.length < 2) return (Array.isArray(rows) ? rows : []).map(() => null);

  const meanX = points.reduce((sum, point) => sum + point.index, 0) / points.length;
  const meanY = points.reduce((sum, point) => sum + point.value, 0) / points.length;
  const denominator = points.reduce((sum, point) => sum + (point.index - meanX) ** 2, 0);
  if (!denominator) return points.map(() => meanY);
  const slope = points.reduce(
    (sum, point) => sum + (point.index - meanX) * (point.value - meanY),
    0,
  ) / denominator;
  const intercept = meanY - slope * meanX;
  return (Array.isArray(rows) ? rows : []).map((_, index) => intercept + slope * index);
};
