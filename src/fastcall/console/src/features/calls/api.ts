import { api } from '../../api/client';
import { OutboundCall } from '../../api/types';

export function getCall(callId: string, signal?: AbortSignal) {
  return api<OutboundCall>(`/calls/${callId}`, { signal });
}
