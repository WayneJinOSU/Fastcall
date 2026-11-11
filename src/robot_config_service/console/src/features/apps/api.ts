import { api } from '../../api/client';
import { AppConfig, RobotApp } from '../../api/types';

export async function listApps() {
  return api<{ items: RobotApp[] }>('/robot-apps');
}

export function createApp(name: string) {
  return api<RobotApp>('/robot-apps', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export function setAppEnabled(appId: string, enabled: boolean) {
  return api<RobotApp>(`/robot-apps/${appId}/enabled`, {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  });
}

export function emptyConfig(appCode: string, name: string): AppConfig {
  return {
    schema_version: 'robot-app/v1',
    app: { code: appCode, name },
    prompt: { system_template: '' },
    variables: [],
    llm: { provider: '', model: '' },
    flow: {},
    call_policy: { allow_interruptions: true, max_call_duration_sec: 180 },
  };
}
