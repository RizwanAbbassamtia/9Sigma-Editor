import { createBrowserRouter, isRouteErrorResponse, Link, RouterProvider, useRouteError, type RouteObject } from "react-router";

import { AppShell } from "./layout/AppShell";
import type { RouteHandle } from "./layout/nav";
import { EditsPage } from "./pages/EditsPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RendersPage } from "./pages/RendersPage";
import { SettingsPage } from "./pages/SettingsPage";
import { EditorWorkspace } from "./workspace/editor/EditorWorkspace";

/** Shown when a page throws while rendering, so the app never goes blank. */
function RouteError() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error) ? `${error.status} ${error.statusText}` : error instanceof Error ? error.message : "Something went wrong on this page.";
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas p-8 text-ink">
      <div className="max-w-md rounded-card border border-line bg-surface p-8 text-center shadow-card">
        <h1 className="text-lg font-semibold">This page hit a problem</h1>
        <p className="mt-2 text-sm text-ink-muted">{message}</p>
        <Link to="/" className="mt-6 inline-flex h-9 items-center rounded-md bg-accent px-4 text-sm font-semibold text-accent-ink hover:bg-accent-hover">
          Back to My edits
        </Link>
      </div>
    </div>
  );
}

function handle(title: string): RouteHandle {
  return { title };
}

const pages: RouteObject[] = [
  { index: true, element: <EditsPage />, handle: handle("My edits") },
  { path: "renders", element: <RendersPage />, handle: handle("Renders") },
  { path: "settings", element: <SettingsPage />, handle: handle("Settings") },
  { path: "*", element: <NotFoundPage />, handle: handle("Page not found") },
];

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage />, errorElement: <RouteError /> },
  // The Edit room is a full-window workspace with its own rail and top bar.
  { path: "/edit/:id", element: <EditorWorkspace />, errorElement: <RouteError /> },
  // Everything else needs a session; AppShell is the guard.
  { path: "/", element: <AppShell />, errorElement: <RouteError />, children: pages },
]);

export function App() {
  return <RouterProvider router={router} />;
}
