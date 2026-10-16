/** Shape for `GET /api/system/info`. Mirrors backend/sigma_editor/api/system.py. */

export interface SystemInfo {
  version: string;
  platform: string;
  python: string;
  app_data_dir: string;
  projects_dir: string;
  exports_dir: string;
  port: number;
}
