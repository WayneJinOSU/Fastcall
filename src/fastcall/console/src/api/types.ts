export type VariableType = 'string' | 'decimal' | 'integer' | 'boolean' | 'datetime';

export interface VariableDefinition {
  key: string;
  type: VariableType;
  required: boolean;
  sensitive?: boolean;
  max_length?: number;
  default?: string;
  format?: string;
}

export interface AppConfig {
  schema_version: 'robot-app/v1';
  app: { code: string; name: string };
  prompt: { system_template: string };
  variables: VariableDefinition[];
  llm: Record<string, string | number>;
  asr?: Record<string, string | number | boolean>;
  tts?: Record<string, string | number>;
  realtime?: Record<string, string | boolean | Record<string, unknown>>;
  flow: Record<string, any>;
  call_policy: { allow_interruptions: boolean; max_call_duration_sec: number };
}

export interface ReleaseSummary {
  robot_app_version_id: string;
  updated_by: string;
  updated_at: string;
}

export interface RobotApp {
  app_id: string;
  app_code: string;
  name: string;
  enabled: boolean;
  draft: AppConfig;
  draft_revision: number;
  created_at: string;
  updated_at: string;
  current_release: ReleaseSummary | null;
}

export interface ValidationIssue {
  code: string;
  path: string;
  message: string;
}

export interface ValidationReport {
  valid: boolean;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  catalog_version: string;
  runtime_resolution?: { engine: string; adapter: string };
  config_hash?: string;
}

export interface RobotVersion {
  robot_app_version_id: string;
  app_id: string;
  version_no: number;
  source_draft_revision: number;
  config_hash: string;
  schema_version: string;
  runtime_engine: string;
  runtime_adapter: string;
  runtime_catalog_version: string;
  validation_report: ValidationReport;
  validated_at: string;
  created_by: string;
  created_at: string;
  app_config?: AppConfig;
}

export interface CapabilityCatalog {
  catalog_version: string;
  runtime_profiles: Array<{
    provider: string;
    model: string;
    runtime_engine: string;
    runtime_adapter: string;
    remote_runtime_supported: boolean;
    enabled: boolean;
    deprecated: boolean;
  }>;
  asr: Record<string, any>;
  tts: Record<string, any>;
  realtime: Record<string, any>;
}

export interface ConsoleSession {
  actor_id: string;
  csrf_token: string;
}

export interface OutboundCall {
  call_id: string;
  task_id: string;
  contact_id: string;
  call_no: number;
  status: string;
  result_code: string | null;
  retryable: boolean | null;
  robot_version_id: string;
  config_hash: string;
  origination_uuid: string;
  freeswitch_uuid: string | null;
  claim_generation: number;
  session_state: string;
  session_ready_at: string | null;
  dial_phase: string;
  answered_by_human: boolean | null;
  raw_hangup_cause: string | null;
  hangup_source: string | null;
  failure_code: string | null;
  termination_reason: string | null;
  state_version: number;
  created_at: string;
  dial_requested_at: string | null;
  dial_acknowledged_at: string | null;
  dialing_at: string | null;
  ringing_at: string | null;
  answered_at: string | null;
  conversation_started_at: string | null;
  ended_at: string | null;
  duration_sec: number | null;
}

export interface LatencyStats {
  count: number;
  avg_ms: number | null;
  p50_ms: number | null;
  p80_ms: number | null;
  p95_ms: number | null;
  max_ms: number | null;
}

export interface AgentTechnicalRecordListItem {
  record_id: number;
  call_uuid: string;
  call_source: 'fastcall_outbound' | 'console_voice_test' | 'generic_ws' | 'unclassified';
  claim_generation: number | null;
  bot_mode: string;
  robot_code: string;
  robot_version_id: string | null;
  task_start_time: string | null;
  task_duration_sec: number | null;
  is_voicemail: boolean;
  total_cost: number | null;
  asr_model_name: string;
  llm_model_name: string;
  tts_model_name: string;
  llm_dialogue_turns: number | null;
  llm_retry_count: number | null;
  asr_reconnect_count: number | null;
  tts_reconnect_count: number | null;
  created_at: string;
  latency: { asr: LatencyStats; llm: LatencyStats; tts: LatencyStats };
}

export interface ProviderTechnicalConfig {
  provider?: string;
  model?: string;
  locale?: string;
  sample_rate?: number;
}

export interface AgentTechnicalRecordDetail extends AgentTechnicalRecordListItem {
  task_end_time: string | null;
  technical_config: {
    asr: ProviderTechnicalConfig;
    llm: ProviderTechnicalConfig;
    tts: ProviderTechnicalConfig;
    pricing: { version?: string; currency?: string; estimated?: boolean };
  };
  asr_param_config: ProviderTechnicalConfig;
  tts_param_config: ProviderTechnicalConfig;
  asr_ttfb_list: number[];
  llm_ttfb_list: number[];
  tts_ttfb_list: number[];
  asr_cost: number | null;
  llm_cost: number | null;
  tts_cost: number | null;
  asr_total_transcribed_chars: number | null;
  asr_total_audio_duration: number | null;
  asr_connection_requests: number | null;
  llm_total_generated_chars: number | null;
  llm_prompt_tokens: number | null;
  llm_completion_tokens: number | null;
  llm_cache_read_tokens: number | null;
  tts_total_output_duration: number | null;
  tts_total_chars: number | null;
  tts_connection_requests: number | null;
}

export interface AgentReportSummary {
  total_records: number;
  logical_calls: number;
  voicemail_calls: number;
  voicemail_rate: number | null;
  avg_duration_sec: number | null;
  total_duration_sec: number | null;
  avg_dialogue_turns: number | null;
  turns_record_count: number;
  missing_turns_records: number;
  asr_cost: number | null;
  llm_cost: number | null;
  tts_cost: number | null;
  total_cost: number | null;
  cost_record_count: number;
  missing_cost_records: number;
  cost_currency: string;
  cost_estimated: boolean;
  llm_retry_count: number;
  missing_llm_retry_records: number;
  asr_reconnect_count: number;
  missing_asr_reconnect_records: number;
  tts_reconnect_count: number;
  missing_tts_reconnect_records: number;
  latency: { asr: LatencyStats; llm: LatencyStats; tts: LatencyStats };
  latency_sample_records: number;
  latency_total_records: number;
  latency_sample_limit: number;
  latency_truncated: boolean;
}

export interface DailyMetric extends AgentReportSummary { date: string }
export interface DailyReportResponse {
  items: DailyMetric[];
  start_at: string;
  end_at: string;
}
export interface AgentFacets {
  robot_codes: string[];
  bot_modes: string[];
}

export interface AgentRecordListResponse {
  items: AgentTechnicalRecordListItem[];
  summary: AgentReportSummary;
  next_cursor: string | null;
}
