export function formatNumber(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : new Intl.NumberFormat('zh-CN').format(value);
}

export function formatDuration(value: number | null | undefined) {
  if (value === null || value === undefined) return '—';
  if (value < 60) return `${value.toFixed(1)} s`;
  const minutes = Math.floor(value / 60);
  const seconds = Math.round(value % 60);
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function formatMs(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `${formatNumber(value)} ms`;
}

export function formatCost(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `$${value.toFixed(4)}`;
}

export function formatPercent(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : `${value.toFixed(1)}%`;
}
