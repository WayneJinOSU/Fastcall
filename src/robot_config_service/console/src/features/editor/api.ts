import { api } from '../../api/client';
import { AppConfig, CapabilityCatalog, RobotApp, RobotVersion, ValidationReport } from '../../api/types';

export function getApp(appId: string) {
  return api<RobotApp>(`/robot-apps/${appId}`);
}

export function getCapabilities() {
  return api<CapabilityCatalog>('/capabilities/runtime-profiles');
}

export function saveDraft(appId: string, revision: number, config: AppConfig) {
  return api<RobotApp>(`/robot-apps/${appId}/draft`, {
    method: 'PUT', body: JSON.stringify({ draft_revision: revision, config }),
  });
}

export function validateDraft(appId: string, revision: number) {
  return api<ValidationReport>(`/robot-apps/${appId}/validate`, {
    method: 'POST', body: JSON.stringify({ draft_revision: revision }),
  });
}

export function publishDraft(appId: string, revision: number, configHash: string) {
  return api<RobotVersion>(`/robot-apps/${appId}/publish`, {
    method: 'POST', body: JSON.stringify({ draft_revision: revision, config_hash: configHash }),
  });
}

export async function listVersions(appId: string) {
  return api<{ items: RobotVersion[] }>(`/robot-apps/${appId}/versions`);
}

export function getVersion(appId: string, versionId: string) {
  return api<RobotVersion>(`/robot-apps/${appId}/versions/${versionId}`);
}

export function rollbackVersion(appId: string, versionId: string) {
  return api<RobotVersion>(`/robot-apps/${appId}/rollback`, {
    method: 'POST', body: JSON.stringify({ robot_app_version_id: versionId }),
  });
}
