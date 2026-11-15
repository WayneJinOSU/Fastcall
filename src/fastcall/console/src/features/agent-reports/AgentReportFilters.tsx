import { AgentFacets } from '../../api/types';
import DateTimeRangePicker, { AppliedDateRange } from './DateTimeRangePicker';

interface Props {
  range: string;
  startAt?: string;
  endAt?: string;
  robotCode: string;
  botMode: string;
  facets: AgentFacets;
  onChange: (values: Record<string, string>) => void;
  onReset: () => void;
}

export default function AgentReportFilters({ range, startAt, endAt, robotCode, botMode, facets, onChange, onReset }: Props) {
  return <div className="agent-reports-filterbar"><DateTimeRangePicker range={range} startAt={startAt} endAt={endAt} onApply={value => onChange(value as unknown as Record<string, string>)} /><select className="select-control" aria-label="Robot" value={robotCode} onChange={event => onChange({ robot_code: event.target.value })}><option value="">全部 Robot</option>{facets.robot_codes.map(code => <option key={code} value={code}>{code}</option>)}</select><select className="select-control" aria-label="Bot 模式" value={botMode} onChange={event => onChange({ bot_mode: event.target.value })}><option value="">全部模式</option>{facets.bot_modes.map(mode => <option key={mode} value={mode}>{mode}</option>)}</select><button className="button" onClick={onReset}>重置</button></div>;
}
