import { useEffect, useRef, useState } from 'react';
import { calendarDates, naturalDayCount, shiftDateKey, toLocalDateTime, toUtc } from './calendar';

export interface AppliedDateRange {
  range: string;
  start_at: string;
  end_at: string;
}

interface Props {
  range: string;
  startAt?: string;
  endAt?: string;
  onApply: (value: AppliedDateRange) => void;
}

export default function DateTimeRangePicker({ range, startAt, endAt, onApply }: Props) {
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(new Date(2026, 10, 1));
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [startTime, setStartTime] = useState('00:00');
  const [endTime, setEndTime] = useState('23:59');
  const [draftRange, setDraftRange] = useState('custom');
  const [error, setError] = useState('');
  const trigger = useRef<HTMLButtonElement>(null);
  const popover = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape' && open) close();
    }
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [open]);

  function openCalendar() {
    const now = new Date();
    const start = toLocalDateTime(startAt, new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000));
    const end = toLocalDateTime(endAt, now);
    setStartDate(start.date);
    setEndDate(end.date);
    setStartTime(start.time);
    setEndTime(end.time);
    setDraftRange(range || '7d');
    setMonth(new Date(`${start.date}T00:00:00`));
    setError('');
    setOpen(true);
  }

  function close() {
    setOpen(false);
    trigger.current?.focus();
  }

  function selectDate(value: string) {
    if (!startDate || endDate) {
      setStartDate(value);
      setEndDate('');
    } else if (value < startDate) {
      setStartDate(value);
      setEndDate('');
    } else {
      setEndDate(value);
    }
    setDraftRange('custom');
    setError('');
  }

  function shortcut(type: string) {
    const now = new Date();
    const today = toLocalDateTime(undefined, now).date;
    let start = today;
    if (type === 'yesterday') start = shiftDateKey(today, -1);
    if (type === 'week') start = shiftDateKey(today, -6);
    setStartDate(start);
    setEndDate(today);
    setStartTime('00:00');
    setEndTime(type === 'yesterday' ? '00:00' : toLocalDateTime(undefined, now).time);
    setDraftRange(type === 'week' ? '7d' : 'custom');
    setError('');
  }

  function apply() {
    if (!startDate || !endDate) {
      setError('请选择完整的开始和结束日期。');
      return;
    }
    const start = toUtc(startDate, startTime);
    const end = toUtc(endDate, endTime);
    if (start >= end) {
      setError('开始时间必须早于结束时间。');
      return;
    }
    if (end.getTime() > Date.now()) {
      setError('结束时间不得晚于当前时间。');
      return;
    }
    if (naturalDayCount(startDate, endDate, startTime, endTime) > 92) {
      setError('自定义范围最多支持 92 个自然日。');
      return;
    }
    onApply({ range: draftRange, start_at: start.toISOString(), end_at: end.toISOString() });
    close();
  }

  const startLocal = startAt ? toLocalDateTime(startAt, new Date()) : null;
  const endLocal = endAt ? toLocalDateTime(endAt, new Date()) : null;
  const rangeLabel = range === '24h' ? '近 24 小时' : range === '30d' ? '近 30 天' : range === 'custom' ? '自定义' : '近 7 天';
  const label = startLocal && endLocal ? `${rangeLabel} · ${startLocal.date.slice(5)} ${startLocal.time} → ${endLocal.date.slice(5)} ${endLocal.time}` : rangeLabel;
  return <div className="date-range"><button ref={trigger} className="date-button" aria-label="时间范围" onClick={openCalendar}>{label}</button>{open && <><div className="calendar-backdrop" onClick={close} /><div className="calendar-overlay" ref={popover}><div className="calendar-popover" role="dialog" aria-label="自定义日期时间范围"><div className="calendar-left"><div className="calendar-top"><strong>{month.getFullYear()} 年 {month.getMonth() + 1} 月</strong><div className="month-nav"><button aria-label="上个月" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}>‹</button><button aria-label="下个月" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}>›</button></div></div><div className="shortcuts"><button onClick={() => shortcut('today')}>今天</button><button onClick={() => shortcut('yesterday')}>昨天</button><button onClick={() => shortcut('week')}>最近 7 天</button></div><div className="week">{['一', '二', '三', '四', '五', '六', '日'].map(day => <span key={day}>{day}</span>)}</div><div className="calendar-grid">{calendarDates(month).map((value, index) => value ? <button key={value} className={`day ${value === startDate || value === endDate ? 'selected' : startDate && endDate && value > startDate && value < endDate ? 'range' : ''}`} aria-label={value} onClick={() => selectDate(value)}>{Number(value.slice(-2))}</button> : <span key={`empty-${index}`} className="day outside" />)}</div></div><div className="calendar-right"><div className="draft-status">正在设置自定义范围<br /><strong>{startDate || '选择开始日期'} {startTime} → {endDate || '选择结束日期'} {endTime}</strong></div><div className="date-fields"><label className="field-label" htmlFor="start-date">开始日期</label><input id="start-date" type="date" value={startDate} onChange={event => { setStartDate(event.target.value); setDraftRange('custom'); }} /><label className="field-label" htmlFor="start-time">开始时间</label><input id="start-time" type="time" value={startTime} onChange={event => { setStartTime(event.target.value); setDraftRange('custom'); }} /><label className="field-label" htmlFor="end-date">结束日期</label><input id="end-date" type="date" value={endDate} onChange={event => { setEndDate(event.target.value); setDraftRange('custom'); }} /><label className="field-label" htmlFor="end-time">结束时间</label><input id="end-time" type="time" value={endTime} onChange={event => { setEndTime(event.target.value); setDraftRange('custom'); }} /></div><div className="range-explain">按分钟选择；不含结束时刻。最多 92 个自然日，结束时间不得晚于当前时间。</div><div className="calendar-error" role="alert">{error}</div><div className="calendar-actions"><button className="button" onClick={close}>取消</button><button className="button primary" onClick={apply}>应用</button></div><div className="calendar-note">Esc / 点击外部关闭<br />取消不会修改当前已生效条件</div></div></div></div></>}</div>;
}
