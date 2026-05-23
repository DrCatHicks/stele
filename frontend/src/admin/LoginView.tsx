import { useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { ApiError } from '../api';
import { useAuth } from '../auth/AuthContext';

interface LocationState {
  from?: { pathname: string };
}

export function LoginView() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // Where the guard bounced us from, so we can return there after login.
  const from = (location.state as LocationState | null)?.from?.pathname ?? '/admin';

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = (event: FormEvent): void => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    login(email, password)
      .then(() => navigate(from, { replace: true }))
      .catch((err: unknown) => {
        // 401 is the expected wrong-credentials case; keep the message generic
        // to match the API's uniform failure (never reveal which field is wrong).
        setError(
          err instanceof ApiError && err.status === 401
            ? 'Invalid email or password.'
            : 'Login failed. Please try again.',
        );
      })
      .finally(() => setSubmitting(false));
  };

  return (
    <form onSubmit={handleSubmit} style={{ maxWidth: '20rem', margin: '4rem auto' }}>
      <h1>Sign in</h1>
      {error ? (
        <p role="alert" style={{ color: 'crimson' }}>
          {error}
        </p>
      ) : null}
      <label style={{ display: 'block', marginBottom: '0.5rem' }}>
        Email
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="username"
          required
          style={{ width: '100%' }}
        />
      </label>
      <label style={{ display: 'block', marginBottom: '0.5rem' }}>
        Password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
          style={{ width: '100%' }}
        />
      </label>
      <button type="submit" disabled={submitting}>
        {submitting ? 'Signing in…' : 'Sign in'}
      </button>
    </form>
  );
}
