import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getSession,
  signInEmail,
  signUpEmail,
  signInSocialRedirectUrl,
  handleOAuthCallback,
} from '../lib/auth';

export default function Login() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    if (params.get('oauth_callback')) {
      handleOAuthCallback().then((ok) => {
        if (ok) {
          navigate('/', { replace: true });
        } else {
          setError('OAuth sign-in failed. Please try again.');
          history.replaceState({}, '', '/login');
        }
      });
      return;
    }

    const session = getSession();
    if (session) {
      navigate('/', { replace: true });
    }
  }, [navigate]);

  async function handleEmailSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      if (mode === 'signup') {
        await signUpEmail(email, password, name || email.split('@')[0]);
      } else {
        await signInEmail(email, password);
      }
      navigate('/', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication failed');
    } finally {
      setLoading(false);
    }
  }

  async function handleGoogleSignIn(e: React.MouseEvent) {
    e.preventDefault();
    setError('');
    setGoogleLoading(true);
    try {
      const callbackURL = window.location.origin + '/login?oauth_callback=1';
      const redirectUrl = await signInSocialRedirectUrl('google', callbackURL);
      window.location.href = redirectUrl;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to initiate Google sign-in');
      setGoogleLoading(false);
    }
  }

  function toggleMode() {
    setMode(mode === 'signin' ? 'signup' : 'signin');
    setError('');
  }

  return (
    <div className="login-wrap">
      <div className="card">
        <h1>Wallet Scanner</h1>
        <p className="sub">Sign in to view the leaderboard</p>
        {error && <div className="error-msg">{error}</div>}

        <button
          type="button"
          className="btn"
          onClick={handleGoogleSignIn}
          disabled={googleLoading}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
          </svg>
          {googleLoading ? 'Redirecting…' : 'Sign in with Google'}
        </button>

        <div className="divider">or</div>

        <form onSubmit={handleEmailSubmit}>
          {mode === 'signup' && (
            <input
              type="text"
              placeholder="Your name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="input"
              autoComplete="name"
            />
          )}
          <input
            type="email"
            placeholder="Email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="input"
            autoComplete="email"
          />
          <input
            type="password"
            placeholder="Password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="input"
            autoComplete={mode === 'signup' ? 'new-password' : 'current-password'}
          />
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading
              ? 'Please wait…'
              : mode === 'signup'
              ? 'Sign up with Email'
              : 'Sign in with Email'}
          </button>
        </form>

        <p className="toggle-link">
          <span>{mode === 'signin' ? "Don't have an account?" : 'Already have an account?'}</span>{' '}
          <a onClick={toggleMode}>{mode === 'signin' ? 'Sign Up' : 'Sign In'}</a>
        </p>

        <p className="footer-note">predictionscanner.io</p>
      </div>
    </div>
  );
}