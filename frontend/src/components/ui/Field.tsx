import type { ReactNode } from "react";

import { cn } from "../../lib/cn";

export interface FieldProps {
  label: ReactNode;
  /** id of the control */
  htmlFor?: string;
  hint?: ReactNode;
  /** the message shown under the control, in red */
  error?: string | undefined;
  required?: boolean;
  className?: string;
  children: ReactNode;
}

/** Label + control + hint/error, the building block of every form. */
export function Field({ label, htmlFor, hint, error, required, className, children }: FieldProps) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={htmlFor} className="text-[13px] font-medium text-ink-muted">
        {label}
        {required ? <span className="ml-0.5 text-accent-text">*</span> : null}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-fail" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-ink-faint">{hint}</p>
      ) : null}
    </div>
  );
}

/** Two- or three-column responsive grid for form fields. */
export function FieldGrid({ columns = 2, className, children }: { columns?: 1 | 2 | 3; className?: string; children: ReactNode }) {
  return (
    <div
      className={cn(
        "grid gap-x-6 gap-y-5",
        columns === 1 && "grid-cols-1",
        columns === 2 && "grid-cols-1 md:grid-cols-2",
        columns === 3 && "grid-cols-1 md:grid-cols-3",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Section heading inside a long form. */
export function FormSection({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-5">
      <div>
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        {description ? <p className="mt-0.5 text-xs text-ink-muted">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}
