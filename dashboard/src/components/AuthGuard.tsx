import { Navigate } from 'react-router-dom';
import { getSession, signOut } from '../lib/auth';

function isJwtExpired(token: string): boolean {
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    if (typeof payload.exp !== 'number') return false;
    return payload.exp * 1000 < Date.now();
  } catch {
    return true; // malformed token treated as expired
  }
}

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const session = getSession();
  if (!session) return <Navigate to="/login" replace />;
  if (isJwtExpired(session.token)) {
    signOut();
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
}
