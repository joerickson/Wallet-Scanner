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

export async function signOut(): Promise<void> {
  localStorage.removeItem('auth_token');
  localStorage.removeItem('auth_user');
  // Await the server-side sign-out so the session cookie is invalidated before
  // any subsequent sign-in attempt, preventing ExpiredSignatureError on re-login.
  if (NEON_AUTH_URL) {
    await fetch(`${NEON_AUTH_URL}/sign-out`, { method: 'POST', credentials: 'include' }).catch(() => {});
  }
}

export async function signInEmail(email: string, password: string): Promise<void> {
  await signOut();

  let res = await fetch(`${NEON_AUTH_URL}/sign-in/email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ email, password, rememberMe: true }),
  });

  // If the Neon server rejects our request because the expired session cookie
  // wasn't cleared by /sign-out (the server validates the cookie even on sign-out),
  // retry without credentials so the stale cookie isn't sent.
  let sentCredentials = true;
  if (!res.ok) {
    const errData = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (/expired|ExpiredSignature/i.test(String(errData['message'] || ''))) {
      sentCredentials = false;
      res = await fetch(`${NEON_AUTH_URL}/sign-in/email`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'omit',
        body: JSON.stringify({ email, password, rememberMe: true }),
      });
    }
  }

  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  if (!res.ok) throw new Error(String(data['message'] || 'Authentication failed'));
  const user = data['user'] as Record<string, unknown> | undefined;
  if (user) persistUser(user);

  // Prefer the JWT from the sign-in response itself — avoids a /get-session
  // round-trip that could also trip over a stale cookie.
  const session = data['session'] as Record<string, unknown> | undefined;
  const jwtFromBody = (
    (session?.['access_token'] ?? data['access_token'] ?? data['token']) as string | undefined
  );
  let jwt: string | null = res.headers.get('set-auth-jwt') ?? jwtFromBody ?? null;

  if (!jwt && sentCredentials) {
    const sessionRes = await fetch(`${NEON_AUTH_URL}/get-session`, { credentials: 'include' });
    jwt = sessionRes.headers.get('set-auth-jwt');
  }

  if (!jwt) throw new Error('No JWT returned from authentication service');
  localStorage.setItem('auth_token', jwt);
}

export async function signUpEmail(email: string, password: string, name: string): Promise<void> {
  await signOut();
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

  // Prefer the JWT from the sign-up response itself.
  const session = data['session'] as Record<string, unknown> | undefined;
  const jwtFromBody = (
    (session?.['access_token'] ?? data['access_token'] ?? data['token']) as string | undefined
  );
  let jwt: string | null = res.headers.get('set-auth-jwt') ?? jwtFromBody ?? null;

  if (!jwt) {
    const sessionRes = await fetch(`${NEON_AUTH_URL}/get-session`, { credentials: 'include' });
    jwt = sessionRes.headers.get('set-auth-jwt');
  }

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
