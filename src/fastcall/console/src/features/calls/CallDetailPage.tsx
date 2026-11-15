import { useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import EmptyState from '../../components/ui/EmptyState';
import ErrorState from '../../components/ui/ErrorState';
import { getAgentRecord, listAgentRecords } from '../../api/agentRecords';
import { AgentTechnicalRecordDetail, AgentTechnicalRecordListItem, OutboundCall } from '../../api/types';
import { shortId } from '../../utils/datetime';
import AgentRecordPanel from './AgentRecordPanel';
import TelephonyFacts from './TelephonyFacts';
import { getCall } from './api';
import './calls.css';

export default function CallDetailPage() {
  const { callId = '' } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const [call, setCall] = useState<OutboundCall | null>(null);
  const [callLoading, setCallLoading] = useState(true);
  const [callError, setCallError] = useState('');
  const [callReload, setCallReload] = useState(0);
  const [records, setRecords] = useState<AgentTechnicalRecordListItem[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(true);
  const [recordsError, setRecordsError] = useState('');
  const [recordsReload, setRecordsReload] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<AgentTechnicalRecordDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [notice, setNotice] = useState('');
  const requestedRecord = searchParams.get('record') || '';
  const fromReports = searchParams.get('from') === 'reports';

  useEffect(() => {
    const controller = new AbortController();
    setCall(null);
    setCallLoading(true);
    setCallError('');
    getCall(callId, controller.signal).then(setCall).catch(err => {
      if (err.name !== 'AbortError') {
        setCall(null);
        setCallError(err instanceof Error ? err.message : 'Call 加载失败');
      }
    }).finally(() => {
      if (!controller.signal.aborted) setCallLoading(false);
    });
    return () => controller.abort();
  }, [callId, callReload]);

  useEffect(() => {
    const controller = new AbortController();
    setRecords([]);
    setSelectedId(null);
    setDetail(null);
    setNotice('');
    setRecordsLoading(true);
    setRecordsError('');
    listAgentRecords({ source: 'all', call_uuid: callId, limit: 200 }, controller.signal)
      .then(result => setRecords(result.items))
      .catch(err => {
        if (err.name !== 'AbortError') {
          setRecords([]);
          setSelectedId(null);
          setDetail(null);
          setRecordsError(err instanceof Error ? err.message : 'Agent 技术数据暂不可用');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setRecordsLoading(false);
      });
    return () => controller.abort();
  }, [callId, recordsReload]);

  useEffect(() => {
    if (!records.length) {
      setSelectedId(null);
      return;
    }
    const requested = Number(requestedRecord);
    const matched = records.find(item => item.record_id === requested);
    setSelectedId(matched?.record_id || records[0].record_id);
    setNotice(requestedRecord && !matched ? '指定记录不可用，已选择首条可用记录。' : '');
  }, [records, requestedRecord]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    const controller = new AbortController();
    setDetail(null);
    setDetailLoading(true);
    setDetailError('');
    getAgentRecord(selectedId, controller.signal).then(setDetail).catch(err => {
      if (err.name !== 'AbortError') setDetailError(err instanceof Error ? err.message : '记录详情加载失败');
    }).finally(() => {
      if (!controller.signal.aborted) setDetailLoading(false);
    });
    return () => controller.abort();
  }, [selectedId, recordsReload]);

  function selectRecord(recordId: number) {
    setSelectedId(recordId);
    setNotice('');
    const next = new URLSearchParams(searchParams);
    next.set('record', String(recordId));
    setSearchParams(next);
  }

  const backTo = fromReports ? '/reports/agent' : call?.task_id ? `/tasks/${call.task_id}` : '/tasks';
  return <div className="page calls-page"><Link className="back-link" to={backTo}>← {fromReports ? '返回 Agent 技术报表' : call?.task_id ? '返回任务详情' : '返回任务列表'}</Link><section className="call-header"><div className="call-title"><h1 className="mono">{shortId(callId, 24)}</h1>{call && <><span className={`result-badge ${call.status === 'succeeded' ? '' : 'warning'}`}>{call.status}</span>{call.result_code && <span className="subtle-badge">{call.result_code}</span>}</>}</div><div className="call-meta"><span>第 <strong>{call?.call_no || '—'}</strong> 次呼叫</span><span>Robot version <strong className="mono">{shortId(call?.robot_version_id, 14)}</strong></span><span>联系人 <strong className="mono">{shortId(call?.contact_id, 14)}</strong></span><span className="call-meta-note">技术记录与电话事实独立加载</span></div></section><div className="call-grid"><div>{callLoading ? <section className="panel"><EmptyState>正在加载电话事实…</EmptyState></section> : callError ? <ErrorState message={callError} onRetry={() => setCallReload(value => value + 1)} /> : call ? <TelephonyFacts call={call} /> : <EmptyState>暂无电话事实</EmptyState>}</div><AgentRecordPanel records={records} listLoading={recordsLoading} listError={recordsError} selectedId={selectedId} detail={detail} detailLoading={detailLoading} detailError={detailError} notice={notice} onSelect={selectRecord} onRetry={() => setRecordsReload(value => value + 1)} /></div></div>;
}
