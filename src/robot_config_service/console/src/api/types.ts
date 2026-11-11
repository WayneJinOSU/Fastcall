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
