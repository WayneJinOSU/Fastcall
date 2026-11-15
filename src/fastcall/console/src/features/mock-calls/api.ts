import { api } from '../../api/client';
import { OutboundCall } from '../../api/types';

export interface MockCallResult {
  task: {
    task_id: string;
    status: string;
    result_revision: number;
    completed_at: string | null;
  };
  call: OutboundCall | null;
  deliveries: Array<{
    delivery_id: string;
    event_type: string;
    status: string;
    attempt_count: number;
    last_http_status: number | null;
    delivered_at: string | null;
  }>;
}

export function createMockCall(input: {
  app_id: string;
  robot_version_id: string;
  client_request_id: string;
  variables: Record<string, string | number | boolean>;
}) {
  return api<{ task_id: string; ws_url: string; robot_version_id: string }>('/mock-calls', {
    method: 'POST', body: JSON.stringify(input),
  });
}

export function getMockCall(taskId: string) {
  return api<MockCallResult>(`/mock-calls/${taskId}`);
}
