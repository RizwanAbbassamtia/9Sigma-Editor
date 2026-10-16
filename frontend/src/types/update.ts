/**
 * GET /api/system/update (docs/RELEASE-CONTRACT.md section 5).
 * Mirrors UpdateStatus in backend/sigma_editor/updates.py.
 */

export type UpdateState =
  | "unknown"
  | "up_to_date"
  | "available"
  | "check_failed"
  | "downloading"
  | "installing"
  | "install_failed";

export interface UpdateInstaller {
  name: string;
  /** bytes; 0 when GitHub did not say */
  size: number;
  url: string;
}

export interface UpdateProgress {
  /** bytes downloaded so far */
  done: number;
  /** bytes in all; 0 when unknown */
  total: number;
}

export interface UpdateStatus {
  current_version: string;
  status: UpdateState;
  /** a check is running now (status keeps the last answer) */
  checking: boolean;
  /** plain English for the status; "" when there is nothing to say */
  message: string;
  latest_version: string | null;
  /** the release page on GitHub (the Download link) */
  release_url: string | null;
  release_notes: string;
  published_at: string | null;
  installer: UpdateInstaller | null;
  /** Update now can run on this copy right now */
  can_install: boolean;
  /** why not, in plain English ("" when can_install) */
  install_blocker: string;
  /** ISO time of the last finished check */
  checked_at: string | null;
  progress: UpdateProgress | null;
}
