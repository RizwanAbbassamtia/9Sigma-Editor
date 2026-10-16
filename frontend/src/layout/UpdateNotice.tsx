import { AlertCircle, ArrowDownCircle, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";

import { errorMessage } from "../api/client";
import { useCheckForUpdates, useInstallUpdate, useUpdateStatus } from "../api/update";
import { ConfirmDialog } from "../components/ui/Dialog";
import { useToast } from "../components/ui/Toast";
import { cn } from "../lib/cn";
import type { UpdateStatus } from "../types";

const pill = "inline-flex h-8 max-w-[26rem] items-center gap-2 rounded-full border px-3 text-xs font-medium";

function megabytes(bytes: number): string {
  return `${Math.round(bytes / 1_048_576)}`;
}

function progressLabel(status: UpdateStatus): string {
  const progress = status.progress;
  if (!progress || progress.total <= 0) return "Downloading the update...";
  const percent = Math.min(100, Math.floor((progress.done / progress.total) * 100));
  return `Downloading the update: ${percent}% (${megabytes(progress.done)} of ${megabytes(progress.total)} MB)`;
}

function ReleaseLink({ status, label = "Download" }: { status: UpdateStatus; label?: string }) {
  if (!status.release_url) return null;
  return (
    <a
      href={status.release_url}
      target="_blank"
      rel="noreferrer"
      title="Open the release page on GitHub"
      className="inline-flex items-center gap-1 underline-offset-2 hover:underline"
    >
      {label}
      <ExternalLink className="size-3" aria-hidden />
    </a>
  );
}

/**
 * Top-bar notice about a newer 9Sigma Editor (docs/RELEASE-CONTRACT.md section 5): nothing while
 * up to date; "Update available" with Update now and Download; progress while it downloads and
 * installs; a quiet "Update check failed" (click to try again) when GitHub could not be reached.
 */
export function UpdateNotice() {
  const update = useUpdateStatus();
  const install = useInstallUpdate();
  const recheck = useCheckForUpdates();
  const { toast } = useToast();
  const [confirming, setConfirming] = useState(false);
  const status = update.data;
  if (!status) return null;

  const startInstall = () => {
    install.mutate(undefined, {
      onSuccess: () => setConfirming(false),
      onError: (error) => {
        setConfirming(false);
        toast({ tone: "error", title: "The update cannot start yet", description: errorMessage(error) });
      },
    });
  };

  if (status.status === "available") {
    const notes = status.release_notes.trim();
    const tip = [
      status.can_install ? "" : status.install_blocker,
      notes ? `What is new in ${status.latest_version}:\n${notes}` : status.message,
    ]
      .filter(Boolean)
      .join("\n\n");
    return (
      <>
        <div className={cn(pill, "border-accent/40 bg-accent/10 text-ink")} title={tip}>
          <ArrowDownCircle className="size-3.5 shrink-0 text-accent-text" aria-hidden />
          <span className="truncate">Update available: {status.latest_version}</span>
          {status.can_install ? (
            <button
              type="button"
              onClick={() => setConfirming(true)}
              className="shrink-0 rounded-full bg-accent px-2.5 py-0.5 text-[11px] font-semibold text-accent-ink hover:bg-accent-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
            >
              Update now
            </button>
          ) : status.install_blocker ? (
            <span className="sr-only">{status.install_blocker}</span>
          ) : null}
          <ReleaseLink status={status} />
        </div>
        <ConfirmDialog
          open={confirming}
          title={`Update to 9Sigma Editor ${status.latest_version}?`}
          description={
            <div className="space-y-2">
              <p>
                The update downloads (about {status.installer?.size ? megabytes(status.installer.size) : "150"} MB), then 9Sigma Editor closes,
                installs it and opens again by itself. This takes a minute or two.
              </p>
              <p>Your projects, channels and settings are kept. Anything not yet saved on this screen is lost.</p>
              {notes ? <p className="whitespace-pre-line text-ink">What is new: {notes}</p> : null}
            </div>
          }
          confirmLabel="Update now"
          cancelLabel="Not now"
          loading={install.isPending}
          onConfirm={startInstall}
          onCancel={() => setConfirming(false)}
        />
      </>
    );
  }

  if (status.status === "downloading") {
    return (
      <div className={cn(pill, "border-accent/40 bg-accent/10 text-ink")} title={status.message}>
        <Loader2 className="size-3.5 shrink-0 animate-spin text-accent-text" aria-hidden />
        <span className="truncate">{progressLabel(status)}</span>
      </div>
    );
  }

  if (status.status === "installing") {
    return (
      <div className={cn(pill, "border-accent/40 bg-accent/10 text-ink")} title={status.message}>
        <Loader2 className="size-3.5 shrink-0 animate-spin text-accent-text" aria-hidden />
        <span className="truncate">Installing the update. 9Sigma Editor closes and opens again.</span>
      </div>
    );
  }

  if (status.status === "install_failed") {
    return (
      <div className={cn(pill, "border-fail/30 bg-fail/10 text-fail")} title={status.message}>
        <AlertCircle className="size-3.5 shrink-0" aria-hidden />
        <span className="truncate">Update failed</span>
        {status.can_install ? (
          <button type="button" onClick={startInstall} className="shrink-0 underline-offset-2 hover:underline" disabled={install.isPending}>
            Try again
          </button>
        ) : null}
        <ReleaseLink status={status} />
      </div>
    );
  }

  if (status.status === "check_failed") {
    return (
      <button
        type="button"
        onClick={() => recheck.mutate()}
        disabled={recheck.isPending || status.checking}
        title={`${status.message} Click to try again now.`}
        className={cn(pill, "border-line bg-surface-2 text-ink-muted transition-colors hover:text-ink disabled:opacity-60")}
      >
        <RefreshCw className={cn("size-3.5 shrink-0", (recheck.isPending || status.checking) && "animate-spin")} aria-hidden />
        <span className="truncate">Update check failed</span>
      </button>
    );
  }

  return null;
}
