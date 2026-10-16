import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useAuthStatus, useChangePassword, useMe } from "../../api/auth";
import { errorMessage } from "../../api/client";
import { MIN_PASSWORD_LENGTH } from "../../types/auth";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Field } from "../ui/Field";
import { Input } from "../ui/Input";
import { Notice } from "../ui/States";
import { useToast } from "../ui/Toast";

/** Settings > Your account: who is signed in and a new password. */
export function PasswordCard() {
  const me = useMe();
  const status = useAuthStatus();
  const change = useChangePassword();
  const { toast } = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");

  const mismatch = again.length > 0 && next !== again;
  const tooShort = next.length > 0 && next.length < MIN_PASSWORD_LENGTH;
  const ready = current.length > 0 && next.length >= MIN_PASSWORD_LENGTH && next === again && !change.isPending;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    change.mutate(
      { current_password: current, new_password: next },
      {
        onSuccess: () => {
          setCurrent("");
          setNext("");
          setAgain("");
          toast({ tone: "success", title: "Password changed" });
        },
        onError: (error) => toast({ tone: "error", title: "The password was not changed", description: errorMessage(error) }),
      },
    );
  };

  return (
    <Card id="account" title="Your account" description={me.data ? `Signed in as ${me.data.display_name || me.data.username} (${me.data.username}).` : "Who is signed in."}>
      <form onSubmit={submit} className="flex max-w-md flex-col gap-4">
        {me.data?.must_change_password ? (
          <Notice tone="warn" title="Please choose your own password">
            This account still has the password it was given. Choose a new one here.
          </Notice>
        ) : null}
        <Field label="Current password" htmlFor="password-current">
          <Input id="password-current" type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} />
        </Field>
        <Field label="New password" htmlFor="password-new" hint={status.data?.password_rule || `At least ${MIN_PASSWORD_LENGTH} characters.`} error={tooShort ? `Use at least ${MIN_PASSWORD_LENGTH} characters.` : undefined}>
          <Input id="password-new" type="password" autoComplete="new-password" value={next} onChange={(event) => setNext(event.target.value)} />
        </Field>
        <Field label="New password again" htmlFor="password-again" error={mismatch ? "The two passwords are not the same." : undefined}>
          <Input id="password-again" type="password" autoComplete="new-password" value={again} onChange={(event) => setAgain(event.target.value)} />
        </Field>
        <div>
          <Button type="submit" variant="primary" size="sm" icon={<KeyRound />} disabled={!ready} loading={change.isPending}>
            Change password
          </Button>
        </div>
      </form>
    </Card>
  );
}
