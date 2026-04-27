import { getToken } from './auth';

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((options.headers as Record<string, string>) ?? {}),
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(path, { ...options, headers });
  if (!res.ok) {
    if (res.status === 401) {
      const { signOut } = await import('./auth');
      signOut();
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    const text = await res.text().catch(() => '');
    const err = new Error(`API ${res.status}: ${text}`) as Error & { status: number };
    err.status = res.status;
    throw err;
  }
  return res.json() as Promise<T>;
}
