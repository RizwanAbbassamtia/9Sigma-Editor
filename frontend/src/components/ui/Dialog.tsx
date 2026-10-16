import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";

import { cn } from "../../lib/cn";
import { Button } from "./Button";

export interface DialogProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  className?: string;
}

/** A width in `className` (max-w-lg, max-w-4xl!, ...) replaces the default max-w-md. */
const OWN_WIDTH = /(^|\s)!?max-w-/;

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type=hidden])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
  "[contenteditable=true]",
].join(", ");

/** What Tab can reach inside `root`, in page order (hidden ones left out). */
function focusables(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((node) => !node.closest("[hidden], [inert]") && node.getAttribute("aria-hidden") !== "true");
}

/** Where the focus goes when the dialog opens: `[data-autofocus]`, else the first field, else the dialog itself. */
function initialFocus(root: HTMLElement): HTMLElement {
  const chosen = root.querySelector<HTMLElement>("[data-autofocus], [autofocus]");
  if (chosen) return chosen;
  const field = focusables(root).find((node) => node.matches("input, select, textarea, [contenteditable=true]"));
  return field ?? root;
}

/**
 * Small modal. Escape or a click on the backdrop closes it. Opening moves the focus inside (a
 * field marked `data-autofocus`, else the first field, else the dialog), Tab stays inside while
 * it is open, and closing gives the focus back to what had it before.
 */
/** The element that has the focus now (not the page itself), or null. */
function focusedElement(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const active = document.activeElement;
  return active instanceof HTMLElement && active !== document.body ? active : null;
}

export function Dialog({ open, title, description, children, footer, onClose, className }: DialogProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  // Who had the focus when the dialog opened, noted while rendering (before anything inside
  // the dialog can take the focus), so closing can give it back.
  const [shown, setShown] = useState<{ open: boolean; opener: HTMLElement | null }>({ open: false, opener: null });
  if (shown.open !== open) setShown({ open, opener: open ? focusedElement() : null });
  const opener = shown.opener;

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    // A field inside may have taken the focus already (its own autoFocus or effect): keep that.
    if (panel && !panel.contains(document.activeElement)) initialFocus(panel).focus({ preventScroll: true });
    return () => {
      const active = document.activeElement;
      const lost = !active || active === document.body || Boolean(panel?.contains(active));
      if (lost && opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, [open, opener]);

  const trapTab = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Tab" || !panelRef.current) return;
    const items = focusables(panelRef.current);
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0]!;
    const last = items[items.length - 1]!;
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === panelRef.current)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-navy/60 p-4 backdrop-blur-[2px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
        tabIndex={-1}
        onKeyDown={trapTab}
        // Without tailwind-merge both widths would land in the class list, and the stylesheet
        // order (not the class order) would decide, so the default is left out instead.
        className={cn("w-full rounded-card border border-line bg-surface p-6 shadow-card outline-none", !OWN_WIDTH.test(className ?? "") && "max-w-md", className)}
      >
        <h2 id="dialog-title" className="text-base font-semibold text-ink">
          {title}
        </h2>
        {description ? <div className="mt-2 text-sm text-ink-muted">{description}</div> : null}
        {children ? <div className="mt-4">{children}</div> : null}
        {footer ? <div className="mt-6 flex justify-end gap-2">{footer}</div> : null}
      </div>
    </div>
  );
}

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  tone?: "default" | "danger";
  loading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "default",
  loading = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      title={title}
      description={description}
      onClose={onCancel}
      footer={
        <>
          <Button variant="secondary" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button variant={tone === "danger" ? "danger" : "primary"} onClick={onConfirm} loading={loading}>
            {confirmLabel}
          </Button>
        </>
      }
    />
  );
}
