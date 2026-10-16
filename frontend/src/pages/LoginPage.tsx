import { LogIn } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useLocation, useSearchParams } from "react-router";

import { useAuthStatus, useLogin, useMe } from "../api/auth";
import { ApiError, errorMessage } from "../api/client";
import { Button } from "../components/ui/Button";
import { Field } from "../components/ui/Field";
import { Input } from "../components/ui/Input";
import { Notice } from "../components/ui/States";

function loginError(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) return "Wrong user name or password.";
  return errorMessage(error);
}

/** `/login`: the sign-in page (docs/CONTRACT-v0.1.md section 0.3). */
export function LoginPage() {
  const me = useMe();
  const status = useAuthStatus();
  const login = useLogin();
  const location = useLocation();
  const [params] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);

  const from = (location.state as { from?: string } | null)?.from;
  const target = from && from !== "/login" ? from : "/";

  useEffect(() => {
    document.title = "Sign in - 9Sigma Editor";
  }, []);

  if (me.data) return <Navigate to={target} replace />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password) return;
    login.mutate({ username: username.trim(), password, remember });
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas p-6 text-ink">
      <form onSubmit={submit} className="w-full max-w-sm rounded-card border border-line bg-surface p-8 shadow-card">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-md bg-accent text-lg font-bold text-accent-ink" aria-hidden>
            9
          </div>
          <div>
            <h1 className="text-lg font-semibold">9Sigma Editor</h1>
            <p className="text-xs text-ink-muted">Sign in to start editing.</p>
          </div>
        </div>

        {params.get("ended") ? (
          <Notice tone="info" className="mb-4">
            You were signed out. Sign in again to carry on.
          </Notice>
        ) : null}

        <div className="flex flex-col gap-4">
          <Field label="User name" htmlFor="username" required>
            <Input id="username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} autoFocus data-autofocus />
          </Field>
          <Field label="Password" htmlFor="password" required hint={status.data?.password_rule || undefined}>
            <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </Field>
          <label className="flex items-center gap-2 text-[13px] text-ink">
            <input type="checkbox" className="accent-accent" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
            Keep me signed in on this computer
          </label>
          {login.isError ? (
            <p className="text-[13px] text-fail" role="alert">
              {loginError(login.error)}
            </p>
          ) : null}
          <Button type="submit" variant="primary" icon={<LogIn />} loading={login.isPending} disabled={!username.trim() || !password} className="w-full">
            Sign in
          </Button>
        </div>
      </form>
    </div>
  );
}
