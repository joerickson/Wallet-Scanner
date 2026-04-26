const NEON_AUTH_URL = ((import.meta.env.VITE_NEON_AUTH_URL as string) || '').replace(/\/$/, '');

export interface UserSession {
  token: string;
  user: {
    id: string;
    email: string;
    name?: string;
  };
}

function parseJwt(token: string): Record<string, unknown> | null {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(window.atob(base64));
  } catch {
    return null;
  }
}

export function getToken(): string | null {
  return localStorage.getItem('auth_token');
}

export function getSession(): UserSession | null {
  const token = getToken();
  if (!token) return null;
  const payload = parseJwt(token);
  if (!payload) return null;
  if (payload['exp'] && (payload['exp'] as number) * 1000 < Date.now()) {
    localStorage.removeItem('auth_token');
    return null;
  }
  return {
    token,
    user: {
      id: String(payload['sub'] || ''),
      email: String(payload['email'] || ''),
      name: payload['name'] ? String(payload['name']) : undefined,
    },
  };
}

export function isAuthConfigured(): boolean {
  return Boolean(NEON_AUTH_URL);
}

export function signOut(): void {
  localStorage.removeItem('auth_token');
}

export async function signInEmail(email: string, password: string): Promise<void> {
  const res = await fetch(`${NEON_AUTH_URL}/sign-in/email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ email, password, rememberMe: true }),
  });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) throw new Error(String(data['message'] || 'Authentication failed'));
  const token = data['token'] as string | undefined;
  if (!token) throw new Error('No token returned from authentication service');
  localStorage.setItem('auth_token', token);
}

export async function signUpEmail(email: string, password: string, name: string): Promise<void> {
  const res = await fetch(`${NEON_AUTH_URL}/sign-up/email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ email, password, name }),
  });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) throw new Error(String(data['message'] || 'Sign-up failed'));
  const token = data['token'] as string | undefined;
  if (!token) throw new Error('No token returned from authentication service');
  localStorage.setItem('auth_token', token);
}

export async function signInSocialRedirectUrl(
  provider: string,
  callbackURL: string,
): Promise<string> {
  const res = await fetch(`${NEON_AUTH_URL}/sign-in/social`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ provider, callbackURL }),
  });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) throw new Error('Failed to initiate social sign-in');
  const redirectUrl = (data['url'] || data['redirect']) as string | undefined;
  if (!redirectUrl) throw new Error('No redirect URL returned');
  return redirectUrl;
}

export async function handleOAuthCallback(): Promise<boolean> {
  try {
    const res = await fetch(`${NEON_AUTH_URL}/get-session`, { credentials: 'include' });
    if (res.ok) {
      const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      const session = data['session'] as Record<string, unknown> | undefined;
      const token = ((session && session['token']) || data['token']) as string | undefined;
      if (token) {
        localStorage.setItem('auth_token', token);
        return true;
      }
    }
  } catch {
    // silently ignore
  }
  return false;
}
