import { Navigate } from 'react-router-dom';
import { getSession, isAuthConfigured } from '../lib/auth';

export function AuthGuard({ children }: { children: React.ReactNode }) {
  if (!isAuthConfigured()) return <>{children}</>;
  const session = getSession();
  if (!session) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
