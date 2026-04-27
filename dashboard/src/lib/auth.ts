const NEON_AUTH_URL = ((import.meta.env.VITE_NEON_AUTH_URL as string) || '').replace(/\/$/, '');

export interface UserSession {
  token: string;
  user: {
    id: string;
    email: string;
    name?: string;
  };
}

export function getToken(): string | null {
  return localStorage.getItem('auth_token');
}

function persistUser(user: Record<string, unknown>): void {
  localStorage.setItem(
    'auth_user',
    JSON.stringify({
      id: String(user['id'] || ''),
      email: String(user['email'] || ''),
      ...(user['name'] ? { name: String(user['name']) } : {}),
    }),
  );
}

export function getSession(): UserSession | null {
  const token = localStorage.getItem('auth_token');
  const userRaw = localStorage.getItem('auth_user');
  if (!token || !userRaw) return null;
  try {
    const user = JSON.parse(userRaw) as { id: string; email: string; name?: string };
    return { token, user };
  } catch {
    return null;
  }
}

export function isAuthConfigured(): boolean {
  return Boolean(NEON_AUTH_URL);
}

export function signOut(): void {
  localStorage.removeItem('auth_token');
  localStorage.removeItem('auth_user');
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
  const user = data['user'] as Record<string, unknown> | undefined;
  if (user) persistUser(user);
  const sessionRes = await fetch(`${NEON_AUTH_URL}/get-session`, { credentials: 'include' });
  const jwt = sessionRes.headers.get('set-auth-jwt');
  if (!jwt) throw new Error('No JWT returned from authentication service');
  localStorage.setItem('auth_token', jwt);
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
  const user = data['user'] as Record<string, unknown> | undefined;
  if (user) persistUser(user);
  const sessionRes = await fetch(`${NEON_AUTH_URL}/get-session`, { credentials: 'include' });
  const jwt = sessionRes.headers.get('set-auth-jwt');
  if (!jwt) throw new Error('No JWT returned from authentication service');
  localStorage.setItem('auth_token', jwt);
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
      const jwt = res.headers.get('set-auth-jwt');
      if (jwt) {
        localStorage.setItem('auth_token', jwt);
        const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        const session = data['session'] as Record<string, unknown> | undefined;
        const user = (data['user'] || (session && session['user'])) as
          | Record<string, unknown>
          | undefined;
        if (user) persistUser(user);
        return true;
      }
    }
  } catch {
    // silently ignore
  }
  return false;
}
