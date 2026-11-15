import { useState } from 'react';
import { formatNumber } from '../../utils/numbers';

export interface TrendSeries {
  name: string;
  values: Array<number | null>;
  color?: string;
  dash?: string;
  width?: number;
}

interface Props {
  title: string;
  subtitle: string;
  days: string[];
  series: TrendSeries[];
  unit?: string;
  truncated?: boolean;
}

const colors = ['#155eef', '#7a5af8', '#12b76a'];

export default function AgentTrendChart({ title, subtitle, days, series, unit = '', truncated = false }: Props) {
  const [hoveredDay, setHoveredDay] = useState<number | null>(null);
  const hasValues = series.some(line => line.values.some(value => value !== null));
  const max = Math.max(...series.flatMap(line => line.values.filter(value => value !== null)), 0) * 1.2 || 1;
  const x = (index: number) => 70 + index * (960 / Math.max(days.length - 1, 1));
  const y = (value: number) => 195 - value / max * 160;
  const latest = series.length > 6 ? series.filter(line => line.name.endsWith('P95')) : series;
  const tickIndexes = new Set(days.length <= 8
    ? days.map((_, index) => index)
    : Array.from({ length: 8 }, (_, index) => Math.round(index * (days.length - 1) / 7)));

  return <section className="panel agent-trend-panel">
    <div className="panel-head"><span className="panel-title">{title}</span><span className="panel-subtitle">{subtitle}</span><span className="agent-trend-latest">最新<strong>{latest.map(line => {
      const value = [...line.values].reverse().find(item => item !== null);
      return `${line.name} ${formatNumber(value)}${value === null || value === undefined ? '' : unit}`;
    }).join(' · ')}</strong></span></div>
    {hasValues ? <>
      <div className="agent-trend-chart-body">
        <svg viewBox="0 0 1080 230" preserveAspectRatio="none" role="img" aria-label={`${title}按天趋势`} onMouseLeave={() => setHoveredDay(null)}>
          <g stroke="#eef0f3" strokeWidth="1">{[0, 1, 2, 3, 4].map(index => <line key={index} x1="70" y1={195 - index * 40} x2="1030" y2={195 - index * 40} />)}</g>
          <line x1="70" y1="35" x2="70" y2="195" stroke="#e4e7ec" />
          <g className="agent-trend-y-ticks" fill="#98a2b3" fontSize="9">{[0, 1, 2, 3, 4].map(index => {
            const value = max / 4 * index;
            const label = value >= 100 ? Math.round(value) : Math.round(value * 100) / 100;
            return <text key={index} x="12" y={198 - index * 40}>{formatNumber(label)}{index === 4 && unit ? unit : ''}</text>;
          })}</g>
          <g fill="#98a2b3" fontSize="10">{days.map((day, index) => tickIndexes.has(index) ? <text key={day} x={x(index) - 15} y="224">{day.slice(5).replace('-', '/')}</text> : null)}</g>
          {series.map((line, seriesIndex) => {
            const color = line.color || colors[seriesIndex % colors.length];
            return <g key={line.name}>
              {line.values.map((value, index) => {
                if (value === null) return null;
                const next = line.values[index + 1];
                if (next === null || next === undefined) return null;
                return <line key={`${line.name}-line-${index}`} x1={x(index)} y1={y(value)} x2={x(index + 1)} y2={y(next)} stroke={color} strokeWidth={line.width || 2.3} strokeDasharray={line.dash} />;
              })}
              {line.values.map((value, index) => value === null ? null : <circle
                key={`${line.name}-${days[index]}`}
                cx={x(index)}
                cy={y(value)}
                r="5"
                fill="#fff"
                stroke={color}
                strokeWidth="2"
                tabIndex={0}
                aria-label={`${days[index]} ${line.name} ${value}${unit}`}
                onMouseEnter={() => setHoveredDay(index)}
                onFocus={() => setHoveredDay(index)}
                onBlur={() => setHoveredDay(null)}
              ><title>{days[index]} · {line.name}: {value}{unit}</title></circle>)}
            </g>;
          })}
        </svg>
        {hoveredDay !== null && <div className="agent-trend-hover" data-testid="chart-hover-tooltip" style={{ left: `${Math.min(Math.max(x(hoveredDay) / 1080 * 100, 12), 76)}%` }}>
          <strong>{days[hoveredDay]}</strong>
          {series.map(line => <div key={line.name}><span>{line.name}</span><strong>{formatNumber(line.values[hoveredDay])}{unit}</strong></div>)}
        </div>}
      </div>
      <div className="agent-trend-note"><span>按天 · 空值不绘制为 0</span><div className="agent-trend-legend">{series.length > 6 ? <><span>ASR / LLM / TTS 使用不同颜色</span><span>平均/P95 实线 · P50/P80 虚线</span></> : series.map((line, index) => <span key={line.name}><i style={{ background: line.color || colors[index % colors.length] }} />{line.name}</span>)}</div></div>
    </> : <div className="empty-state">当前时间范围暂无有效指标数据</div>}
    {truncated && <div className="agent-trend-truncated">部分日期使用有界样本计算，详见每日报表。</div>}
  </section>;
}
