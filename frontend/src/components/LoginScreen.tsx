import { useState } from "react";
import type { FormEvent } from "react";

type LoginScreenProps = {
  onLogin: (password: string) => Promise<void>;
  error: string | null;
};

export function LoginScreen({ onLogin, error }: LoginScreenProps) {
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!password || submitting) return;

    setSubmitting(true);
    try {
      await onLogin(password);
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="login-screen">
      <form className="login-form" onSubmit={handleSubmit}>
        <div className="login-title">MINDCORE</div>
        <label htmlFor="private-access-password">Private access password</label>
        <input
          id="private-access-password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          autoFocus
          required
        />
        {error && <div className="error-banner">{error}</div>}
        <button className="login-button" type="submit" disabled={submitting}>
          {submitting ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </main>
  );
}
