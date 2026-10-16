import { Clapperboard, Film, Settings, type LucideIcon } from "lucide-react";

/** What a route tells the shell: the title shown in the top bar. */
export interface RouteHandle {
  title?: string;
}

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** the item is lit for every address under `to` */
  prefix?: string;
}

/** The three places of the app (docs/CONTRACT-v0.1.md section 2), in rail order. */
export const NAV_ITEMS: readonly NavItem[] = [
  { to: "/", label: "My edits", icon: Clapperboard, prefix: "/edit" },
  { to: "/renders", label: "Renders", icon: Film },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function editorPath(projectId: string): string {
  return `/edit/${encodeURIComponent(projectId)}`;
}

export const RENDERS_PATH = "/renders";
export const SETTINGS_PATH = "/settings";
