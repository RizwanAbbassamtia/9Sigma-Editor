import { LogOut, Moon, Sun } from "lucide-react";
import { useEffect } from "react";
import { Navigate, NavLink, Outlet, useLocation, useMatches, useNavigate } from "react-router";

import { useLogout, useMe, useSessionEnded } from "../api/auth";
import { Spinner } from "../components/ui/Spinner";
import { cn } from "../lib/cn";
import { useTheme } from "../lib/theme";
import { DoctorPill } from "./DoctorPill";
import { NAV_ITEMS, type RouteHandle } from "./nav";
import { UpdateNotice } from "./UpdateNotice";

function pageTitle(matches: ReturnType<typeof useMatches>): string {
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const handle = matches[i]?.handle as RouteHandle | undefined;
    if (handle?.title) return handle.title;
  }
  return "9Sigma Editor";
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0]!.slice(0, 2).toUpperCase();
  return (parts[0]![0]! + parts[parts.length - 1]![0]!).toUpperCase();
}

/**
 * The app frame for signed-in people: a 48 px rail on the left (My edits, Renders, Settings,
 * and at the bottom who is signed in with Sign out), a slim top bar with the page title, the
 * health pill and the update notice, and the page itself. Nobody signed in: the sign-in page.
 */
export function AppShell() {
  const me = useMe();
  const ended = useSessionEnded();
  const logout = useLogout();
  const location = useLocation();
  const navigate = useNavigate();
  const matches = useMatches();
  const { theme, toggle } = useTheme();
  const title = pageTitle(matches);

  useEffect(() => {
    document.title = title === "9Sigma Editor" ? title : `${title} - 9Sigma Editor`;
  }, [title]);

  useEffect(() => {
    if (ended) navigate("/login?ended=1", { replace: true, state: { from: location.pathname } });
  }, [ended, navigate, location.pathname]);

  if (me.isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas text-ink-muted" role="status">
        <Spinner />
        <span className="ml-3 text-sm">Opening 9Sigma Editor...</span>
      </div>
    );
  }

  if (me.isError || me.data === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  const user = me.data;

  return (
    <div className="flex min-h-screen bg-canvas text-ink">
      <nav className="fixed inset-y-0 left-0 z-30 flex w-12 flex-col items-center border-r border-line bg-surface py-2" aria-label="Main">
        <div className="mb-2 flex size-9 items-center justify-center rounded-md bg-accent text-sm font-bold text-accent-ink" title="9Sigma Editor" aria-hidden>
          9
        </div>
        <ul className="flex flex-1 flex-col items-center gap-1">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const lit = item.to === "/" ? location.pathname === "/" || (item.prefix ? location.pathname.startsWith(item.prefix) : false) : location.pathname.startsWith(item.to);
            return (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === "/"}
                  title={item.label}
                  aria-label={item.label}
                  className={cn(
                    "flex size-9 items-center justify-center rounded-md transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60",
                    lit ? "bg-accent/15 text-accent-text" : "text-ink-muted hover:bg-surface-2 hover:text-ink",
                  )}
                >
                  <Icon className="size-[18px]" aria-hidden />
                </NavLink>
              </li>
            );
          })}
        </ul>
        <div className="flex flex-col items-center gap-1">
          <button
            type="button"
            onClick={toggle}
            title={theme === "dark" ? "Switch to the light look" : "Switch to the dark look"}
            aria-label={theme === "dark" ? "Switch to the light look" : "Switch to the dark look"}
            className="flex size-9 items-center justify-center rounded-md text-ink-muted hover:bg-surface-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
          >
            {theme === "dark" ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
          </button>
          <div
            className="flex size-8 items-center justify-center rounded-full bg-surface-2 text-[11px] font-semibold text-ink"
            title={`Signed in as ${user.display_name || user.username}`}
            aria-label={`Signed in as ${user.display_name || user.username}`}
          >
            {initials(user.display_name || user.username)}
          </div>
          <button
            type="button"
            onClick={() => logout.mutate(undefined, { onSettled: () => navigate("/login", { replace: true }) })}
            disabled={logout.isPending}
            title="Sign out"
            aria-label="Sign out"
            className="flex size-9 items-center justify-center rounded-md text-ink-muted hover:bg-surface-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 disabled:opacity-50"
          >
            <LogOut className="size-4" aria-hidden />
          </button>
        </div>
      </nav>

      <div className="flex min-h-screen min-w-0 flex-1 flex-col pl-12">
        <header className="sticky top-0 z-20 flex h-12 items-center justify-between gap-4 border-b border-line bg-surface/95 px-6 backdrop-blur">
          <h1 className="truncate text-sm font-semibold text-ink">{title}</h1>
          <div className="flex items-center gap-2">
            <UpdateNotice />
            <DoctorPill />
          </div>
        </header>
        <main className="flex-1 px-6 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
