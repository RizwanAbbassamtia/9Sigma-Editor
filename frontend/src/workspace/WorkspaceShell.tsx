import { LogOut } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router";

import { useLogout, useMe } from "../api/auth";
import { NAV_ITEMS } from "../layout/nav";
import { cn } from "../lib/cn";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}

/**
 * The full-window frame of the Edit room (docs/design/EDITOR-DESIGN.md section 2.1): a 48 px
 * app rail, a 44 px top bar the page fills, and the page below. No page scroll; the dark
 * workspace theme (`.workspace` in index.css) applies to everything inside, and to dialogs
 * that portal to <body> while it is open.
 */
export function WorkspaceShell({ topBar, children }: { topBar: ReactNode; children: ReactNode }) {
  const me = useMe();
  const logout = useLogout();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    document.body.classList.add("workspace-open");
    return () => document.body.classList.remove("workspace-open");
  }, []);

  const user = me.data;

  return (
    <div className="workspace flex h-screen min-w-[1100px] overflow-hidden text-[12px] leading-4 text-ink">
      <nav className="flex w-12 shrink-0 flex-col items-center border-r border-ed-line bg-ed-canvas py-2" aria-label="Main">
        <div className="mb-2 flex size-9 items-center justify-center rounded-md bg-accent text-sm font-bold text-accent-ink" title="9Sigma Editor" aria-hidden>
          9
        </div>
        <ul className="flex flex-1 flex-col items-center gap-1">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const lit = item.to === "/" ? location.pathname.startsWith("/edit") : location.pathname.startsWith(item.to);
            return (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === "/"}
                  title={item.label}
                  aria-label={item.label}
                  className={cn(
                    "ws-focus flex size-9 items-center justify-center rounded-md transition-colors",
                    lit ? "bg-accent/15 text-accent-text" : "text-ink-muted hover:bg-ed-raised hover:text-ink",
                  )}
                >
                  <Icon className="size-[18px]" aria-hidden />
                </NavLink>
              </li>
            );
          })}
        </ul>
        <div className="flex flex-col items-center gap-1">
          {user ? (
            <div className="flex size-8 items-center justify-center rounded-full bg-ed-raised text-[11px] font-semibold text-ink" title={`Signed in as ${user.display_name || user.username}`}>
              {initials(user.display_name || user.username)}
            </div>
          ) : null}
          <button
            type="button"
            onClick={() => logout.mutate(undefined, { onSettled: () => navigate("/login", { replace: true }) })}
            disabled={logout.isPending}
            title="Sign out"
            aria-label="Sign out"
            className="ws-focus flex size-9 items-center justify-center rounded-md text-ink-muted hover:bg-ed-raised hover:text-ink disabled:opacity-50"
          >
            <LogOut className="size-4" aria-hidden />
          </button>
        </div>
      </nav>
      <div className="flex min-w-0 flex-1 flex-col bg-ed-canvas">
        <header className="flex h-11 shrink-0 items-center border-b border-ed-line bg-ed-canvas">{topBar}</header>
        <div className="flex min-h-0 flex-1">{children}</div>
      </div>
    </div>
  );
}
