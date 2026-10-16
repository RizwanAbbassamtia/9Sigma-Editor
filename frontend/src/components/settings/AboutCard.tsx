import { RefreshCw } from "lucide-react";

import { errorMessage } from "../../api/client";
import { useSystemInfo } from "../../api/system";
import { useCheckForUpdates, useUpdateStatus } from "../../api/update";
import { formatDateTime } from "../../lib/format";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { useToast } from "../ui/Toast";

/** Settings > About: the version, the computer and the update check. */
export function AboutCard() {
  const system = useSystemInfo();
  const update = useUpdateStatus();
  const check = useCheckForUpdates();
  const { toast } = useToast();
  const status = update.data;

  const updateLine = !status
    ? "Checking for updates..."
    : status.status === "up_to_date"
      ? "This is the latest version."
      : status.status === "available"
        ? `Version ${status.latest_version} is available (see the notice at the top).`
        : status.message || "No update information yet.";

  return (
    <Card
      id="about"
      title="About"
      description="This copy of 9Sigma Editor."
      actions={
        <Button
          variant="secondary"
          size="sm"
          icon={<RefreshCw />}
          loading={check.isPending || Boolean(status?.checking)}
          onClick={() => check.mutate(undefined, { onError: (error) => toast({ tone: "error", title: "The update check failed", description: errorMessage(error) }) })}
        >
          Check for updates
        </Button>
      }
    >
      <dl className="grid gap-x-6 gap-y-2 text-[13px] md:grid-cols-[200px_1fr]">
        <dt className="text-ink-muted">Version</dt>
        <dd className="text-ink">{system.data?.version ?? "..."}</dd>
        <dt className="text-ink-muted">Updates</dt>
        <dd className="text-ink">
          {updateLine}
          {status?.checked_at ? <span className="ml-2 text-xs text-ink-faint">Last checked {formatDateTime(status.checked_at)}</span> : null}
        </dd>
        <dt className="text-ink-muted">Computer</dt>
        <dd className="text-ink">
          {system.data ? `${system.data.platform}, Python ${system.data.python}, port ${system.data.port}` : "..."}
        </dd>
        <dt className="text-ink-muted">App data</dt>
        <dd className="font-mono text-[12px] text-ink">{system.data?.app_data_dir ?? "..."}</dd>
      </dl>
    </Card>
  );
}
