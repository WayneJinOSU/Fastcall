import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import ErrorState from '../../components/ui/ErrorState';
import {
  AgentFacets,
  AgentReportSummary as Summary,
  DailyMetric,
} from '../../api/types';
import AgentDailyTable from './AgentDailyTable';
import AgentReportFilters from './AgentReportFilters';
import AgentReportSummary from './AgentReportSummary';
import AgentTrendChart, { TrendSeries } from './AgentTrendChart';
import { getAgentDaily, getAgentFacets, getAgentSummary } from './api';
import './agent-reports.css';

const ranges = { '24h': 24 * 60 * 60 * 1000, '7d': 7 * 24 * 60 * 60 * 1000, '30d': 30 * 24 * 60 * 60 * 1000 };

function timeBoundary(range: string) {
  const end = new Date();
  const start = new Date(end.getTime() - (ranges[range as keyof typeof ranges] || ranges['7d']));
  return { range, start_at: start.toISOString(), end_at: end.toISOString() };
}

function chartDefinitions(items: DailyMetric[]) {
  const days = items.map(item => item.date);
  const line = (name: string, values: Array<number | null>): TrendSeries => ({ name, values });
  const ttfb: TrendSeries[] = [];
  const providers = [
    { name: 'ASR', key: 'asr' as const, color: '#155eef' },
    { name: 'LLM', key: 'llm' as const, color: '#7a5af8' },
    { name: 'TTS', key: 'tts' as const, color: '#12b76a' },
  ];
  ['avg', 'p50', 'p80', 'p95'].forEach(stat => providers.forEach(provider => ttfb.push({
    name: `${provider.name} ${stat === 'avg' ? '平均' : stat.toUpperCase()}`,
    values: items.map(item => item.latency[provider.key][
      `${stat}_ms` as 'avg_ms' | 'p50_ms' | 'p80_ms' | 'p95_ms'
    ]),
    color: provider.color,
    dash: stat === 'p50' ? '3 4' : stat === 'p80' ? '8 4' : '',
    width: stat === 'p95' ? 2.6 : 1.4,
  })));
  const reconnects = items.map(item => item.total_records > 0 && (
    item.missing_asr_reconnect_records === item.total_records
    || item.missing_tts_reconnect_records === item.total_records
  ) ? null : item.asr_reconnect_count + item.tts_reconnect_count);
  return [
    { title: '会话量', subtitle: '逻辑 Call 与技术记录', unit: '', series: [line('逻辑 Call', items.map(item => item.logical_calls)), line('技术记录', items.map(item => item.total_records))] },
    { title: '语音信箱占比', subtitle: '按逻辑 Call 去重', unit: '%', series: [line('语音信箱占比', items.map(item => item.voicemail_rate))] },
    { title: '平均 Agent 会话时长', subtitle: '按实际会话开始时间', unit: ' s', series: [line('平均会话', items.map(item => item.avg_duration_sec))] },
    { title: '平均对话轮次', subtitle: '仅统计轮次非空记录', unit: ' 轮', series: [line('平均轮次', items.map(item => item.avg_dialogue_turns))] },
    { title: '成本估算', subtitle: 'USD · 非供应商账单', unit: ' USD', series: [line('成本', items.map(item => item.total_cost))] },
    { title: '重试与重连', subtitle: 'LLM 重试 / ASR + TTS 重连', unit: '', series: [line('LLM 重试', items.map(item => item.total_records > 0 && item.missing_llm_retry_records === item.total_records ? null : item.llm_retry_count)), line('ASR/TTS 重连', reconnects)] },
    { title: 'TTFB 延迟分布', subtitle: '平均 / P50 / P80 / P95 · ASR / LLM / TTS', unit: ' ms', series: ttfb },
  ];
}

export default function AgentReportsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [summary, setSummary] = useState<Summary | null>(null);
  const [daily, setDaily] = useState<DailyMetric[] | null>(null);
  const [facets, setFacets] = useState<AgentFacets>({ robot_codes: [], bot_modes: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const queryKey = searchParams.toString();
  const range = searchParams.get('range') || '7d';

  useEffect(() => {
    const controller = new AbortController();
    getAgentFacets(controller.signal).then(setFacets).catch(() => setFacets({ robot_codes: [], bot_modes: [] }));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!searchParams.get('start_at') || !searchParams.get('end_at')) {
      const next = new URLSearchParams(searchParams);
      Object.entries(timeBoundary(range)).forEach(([key, value]) => next.set(key, value));
      setSearchParams(next, { replace: true });
      return;
    }
    const controller = new AbortController();
    setSummary(null);
    setDaily(null);
    setLoading(true);
    setError('');
    const query = {
      start_at: searchParams.get('start_at') || undefined,
      end_at: searchParams.get('end_at') || undefined,
      robot_code: searchParams.get('robot_code') || undefined,
      bot_mode: searchParams.get('bot_mode') || undefined,
    };
    Promise.all([getAgentSummary(query, controller.signal), getAgentDaily(query, controller.signal)])
      .then(([summaryValue, dailyValue]) => {
        setSummary(summaryValue);
        setDaily(dailyValue.items);
      })
      .catch(err => {
        if (err.name !== 'AbortError') {
          setSummary(null);
          setDaily(null);
          setError(err instanceof Error ? err.message : '通话技术分析加载失败');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [queryKey]);

  function changeFilters(values: Record<string, string>) {
    const next = new URLSearchParams(searchParams);
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    setSearchParams(next);
  }

  function refresh() {
    const next = new URLSearchParams(searchParams);
    Object.entries(timeBoundary(range)).forEach(([key, value]) => next.set(key, value));
    setSearchParams(next);
  }

  function reset() {
    setSearchParams(new URLSearchParams(timeBoundary('7d')));
  }

  const items = daily || [];
  const truncated = items.some(item => item.latency_truncated);
  return <div className="page agent-reports-page"><section className="heading"><div><h1>通话技术分析</h1><p className="description">全局 Agent 会话、语音行为、时延与成本趋势。</p></div><button className="button" onClick={refresh}>刷新</button></section><AgentReportFilters range={range} startAt={searchParams.get('start_at') || undefined} endAt={searchParams.get('end_at') || undefined} robotCode={searchParams.get('robot_code') || ''} botMode={searchParams.get('bot_mode') || ''} facets={facets} onChange={changeFilters} onReset={reset} />{error && <ErrorState message={error} onRetry={refresh} />}{summary && <AgentReportSummary summary={summary} />}{loading ? <div className="empty-state">正在加载分析数据…</div> : daily && <><div className="agent-chart-stack">{chartDefinitions(items).map(definition => <AgentTrendChart key={definition.title} title={definition.title} subtitle={definition.subtitle} unit={definition.unit} days={items.map(item => item.date)} series={definition.series} truncated={truncated} />)}</div><AgentDailyTable items={items} /></>}<div className="page-note">空库与空区间不生成模拟曲线；缺失指标显示 —。</div></div>;
}
