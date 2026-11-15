import { api } from '../../api/client';
import { ContactListResponse, TaskAction, TaskCallListResponse, TaskListResponse, OutboundTask } from './types';

function query(path: string, values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value));
  });
  return `${path}?${params}`;
}

export function listTasks(values: Record<string, string | number | undefined>, signal?: AbortSignal) {
  return api<TaskListResponse>(query('/outbound-tasks', values), { signal });
}

export function getTask(taskId: string, signal?: AbortSignal) {
  return api<OutboundTask>(`/outbound-tasks/${taskId}`, { signal });
}

export function listContacts(taskId: string, values: Record<string, string | number | undefined>, signal?: AbortSignal) {
  return api<ContactListResponse>(query(`/outbound-tasks/${taskId}/contacts`, values), { signal });
}

export function listTaskCalls(taskId: string, values: Record<string, string | number | undefined>, signal?: AbortSignal) {
  return api<TaskCallListResponse>(query(`/outbound-tasks/${taskId}/calls`, values), { signal });
}

export function runTaskAction(taskId: string, action: TaskAction, expectedVersion: number) {
  return api<OutboundTask>(`/outbound-tasks/${taskId}/${action}`, {
    method: 'POST',
    body: JSON.stringify({ expected_state_version: expectedVersion, ...(action === 'cancel' ? { hangup_active: true } : {}) }),
  });
}
