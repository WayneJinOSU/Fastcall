import { OutboundCall } from '../../api/types';

export type TaskStatus = 'draft' | 'scheduled' | 'running' | 'paused' | 'completed' | 'cancelled';

export interface OutboundTask {
  task_id: string;
  customer_task_id: string;
  name: string;
  robot_app_id: string | null;
  robot_code: string | null;
  robot_name: string | null;
  robot_version_id: string | null;
  config_hash: string | null;
  status: TaskStatus;
  health_status: string;
  timezone: string;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  calling_windows: Array<Record<string, unknown>>;
  excluded_dates: string[];
  max_concurrent_calls: number;
  calls_per_minute: number;
  retry_policy: { max_retries?: number; retry_intervals_sec?: number[]; retry_on?: string[] };
  state_version: number;
  last_error_code: string | null;
  contact_statistics: Record<string, number>;
  active_call_count: number;
  unknown_call_count: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface TaskContact {
  contact_id: string;
  task_id: string;
  customer_contact_id: string;
  phone_last4: string;
  status: string;
  retry_count: number;
  active_call_id: string | null;
  next_call_at: string | null;
  result_code: string | null;
  last_error_code: string | null;
  call_count: number;
  last_call_id: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface TaskListResponse { items: OutboundTask[]; next_cursor: string | null }
export interface ContactListResponse { items: TaskContact[]; next_cursor: string | null }
export interface TaskCallListResponse { items: OutboundCall[]; next_cursor: string | null }
export type TaskAction = 'start' | 'pause' | 'resume' | 'cancel';
