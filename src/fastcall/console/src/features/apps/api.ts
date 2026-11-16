import { api } from '../../api/client';
import { RobotApp } from '../../api/types';

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
