import { Navigate } from 'react-router-dom';
import { getSession } from '../lib/auth';

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const session = getSession();
  if (!session) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
