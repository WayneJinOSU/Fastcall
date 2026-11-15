import { api } from './client';
import {
  AgentFacets,
  AgentRecordListResponse,
  AgentReportSummary,
  AgentTechnicalRecordDetail,
  DailyReportResponse,
} from './types';

export interface AgentRecordQuery {
  start_at?: string;
  end_at?: string;
  robot_code?: string;
  bot_mode?: string;
  source?: string;
  call_uuid?: string;
  cursor?: string;
  limit?: number;
}

function queryString(query: AgentRecordQuery) {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value));
  });
  return params.toString();
}

export function listAgentRecords(query: AgentRecordQuery, signal?: AbortSignal) {
  return api<AgentRecordListResponse>(`/agent-call-records?${queryString(query)}`, { signal });
}

export function getAgentSummary(query: AgentRecordQuery, signal?: AbortSignal) {
  return api<AgentReportSummary>(`/agent-call-records/summary?${queryString(query)}`, { signal });
}

export function getAgentDaily(query: AgentRecordQuery, signal?: AbortSignal) {
  return api<DailyReportResponse>(`/agent-call-records/daily?${queryString(query)}`, { signal });
}

export function getAgentFacets(signal?: AbortSignal) {
  return api<AgentFacets>('/agent-call-records/facets', { signal });
}

export function getAgentRecord(recordId: number, signal?: AbortSignal) {
  return api<AgentTechnicalRecordDetail>(`/agent-call-records/${recordId}`, { signal });
}
