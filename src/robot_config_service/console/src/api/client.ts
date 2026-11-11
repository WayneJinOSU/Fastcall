export class ApiError extends Error {
  code: string;
  status: number;
  details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

let csrfToken = '';

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers);
  if (options.body) headers.set('Content-Type', 'application/json');
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && path !== '/login') {
    headers.set('X-CSRF-Token', csrfToken);
  }
  const response = await fetch(`/console/api/v1${path}`, {
    ...options,
    headers,
    credentials: 'same-origin',
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const error = body?.error || {};
    if (response.status === 401 && !['/login', '/session'].includes(path)) {
      window.dispatchEvent(new Event('console:unauthorized'));
    }
    throw new ApiError(response.status, error.code || 'REQUEST_FAILED', error.message || '请求失败', error.details);
  }
  return response.json();
}
