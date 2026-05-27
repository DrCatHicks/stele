import { useState, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { ApiError } from '../api';
import { useAuth } from '../auth/AuthContext';
import { Alert, Button, Card, CardBody, Field } from '../ui';

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
    <div className="flex min-h-screen items-center justify-center px-4">
      <Card className="w-full max-w-sm">
        <CardBody className="flex flex-col gap-4">
          <div>
            <p className="text-sm font-semibold text-brand-dark">Stele</p>
            <h1 className="text-xl font-semibold text-ink">Sign in</h1>
          </div>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            {error ? <Alert tone="error">{error}</Alert> : null}
            <Field
              label="Email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
            />
            <Field
              label="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
            <Button type="submit" disabled={submitting}>
              {submitting ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}
