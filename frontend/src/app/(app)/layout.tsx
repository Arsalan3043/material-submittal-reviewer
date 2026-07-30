"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { BookOpen, ChevronLeft, ChevronRight, LogOut, Plus, Search } from "lucide-react";
import { getStoredTokens, logout } from "@/lib/auth";
import { listProjects, type Project } from "@/lib/api";
import { SessionProvider, useSession } from "@/lib/hooks/use-session";
import { RunningReviewProvider } from "@/lib/hooks/use-running-review";
import { SidebarSearchProvider, useSidebarSearch } from "@/lib/hooks/use-sidebar-search";
import { NewProjectModalProvider, useNewProjectModal } from "@/components/new-project-modal";

const SIDEBAR_COLLAPSED_KEY = "msr_sidebar_collapsed";

/**
 * Persistent app shell (README §2): 236px sidebar with project switcher, search, spec
 * library link (admin-gated) and user row; the header bar itself lives per-page
 * (components/page-header.tsx) so each screen controls its own title/actions while staying
 * visually identical.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [authChecked, setAuthChecked] = useState(false);

  useEffect(() => {
    if (!getStoredTokens()) {
      router.replace("/login");
      return;
    }
    // Deliberate effect, not a lazy useState initializer: localStorage is unavailable
    // during SSR, so reading it outside an effect would render differently on the server
    // (no token) vs. the client hydration pass (token present) — a hydration mismatch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setAuthChecked(true);
  }, [router]);

  if (!authChecked) return null;

  return (
    <SessionProvider>
      <RunningReviewProvider>
        <SidebarSearchProvider>
          <NewProjectModalProvider>
            <AppShell>{children}</AppShell>
          </NewProjectModalProvider>
        </SidebarSearchProvider>
      </RunningReviewProvider>
    </SessionProvider>
  );
}

function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, loading, noTenant } = useSession();
  const { query, setQuery } = useSidebarSearch();
  const { open: openNewProjectModal } = useNewProjectModal();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch(() => setProjects([]));
  }, [pathname]);

  useEffect(() => {
    // Same hydration-safety reasoning as authChecked above — localStorage doesn't exist
    // during SSR, so this has to happen after mount, not in a lazy useState initializer.
    if (localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1") {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCollapsed(true);
    }
  }, []);

  function toggleCollapsed() {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? "1" : "0");
      return next;
    });
  }

  if (noTenant) {
    return (
      <div className="flex flex-1 items-center justify-center bg-canvas px-4">
        <div className="w-full max-w-sm rounded-[13px] border border-line bg-panel p-6 text-center">
          <h1 className="text-lg font-semibold text-ink">
            Your account isn&rsquo;t attached to a tenant yet
          </h1>
          <p className="mt-2 text-sm text-text-secondary">
            Ask your admin to finish provisioning, then sign in again.
          </p>
          <button
            type="button"
            onClick={() => {
              logout();
              router.replace("/login");
            }}
            className="mt-5 h-10 w-full rounded-[10px] border border-line-2 bg-panel text-sm font-medium text-text-secondary hover:bg-[#F4F4F2]"
          >
            Sign out
          </button>
        </div>
      </div>
    );
  }

  const activeProjectId = pathname.match(/^\/projects\/([^/]+)/)?.[1];
  const isAdmin = user?.role === "tenant_admin" || user?.role === "super_admin";
  const initials = (user?.email ?? "??").slice(0, 2).toUpperCase();

  return (
    <div className="flex flex-1 overflow-hidden">
      <aside
        className={`relative hidden shrink-0 flex-col gap-4 border-r border-[#E6E6E3] bg-sidebar pt-[18px] transition-[width] duration-200 md:flex ${
          collapsed ? "w-[64px] items-center px-2" : "w-[236px] p-[14px] pt-[18px]"
        }`}
      >
        {/* Collapse/expand toggle — overlaps the sidebar's right edge, a la Notion/Linear. */}
        <button
          type="button"
          onClick={toggleCollapsed}
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="absolute -right-3 top-6 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-line-2 bg-panel text-text-muted shadow-[0_1px_3px_rgba(0,0,0,.1)] hover:border-accent hover:text-accent"
        >
          {collapsed ? <ChevronRight size={13} strokeWidth={2.5} /> : <ChevronLeft size={13} strokeWidth={2.5} />}
        </button>

        <Link
          href="/projects"
          className={`flex items-center gap-2.5 ${collapsed ? "" : "px-1.5"}`}
          title="Clause"
        >
          <div className="h-[22px] w-[22px] shrink-0 rounded-[6px] bg-accent" />
          {!collapsed && <span className="text-[15px] font-semibold text-ink">Clause</span>}
        </Link>

        {!collapsed && (
          <div className="flex h-[34px] items-center gap-2 rounded-[9px] border border-[#E2E2DF] bg-panel px-2.5">
            <Search size={13} strokeWidth={2} className="shrink-0 text-text-faint" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search submittals"
              className="min-w-0 flex-1 border-none bg-transparent text-[13px] text-ink outline-none placeholder:text-text-faint"
            />
          </div>
        )}

        <div className={`flex min-h-0 flex-col gap-1 overflow-hidden ${collapsed ? "items-center" : ""}`}>
          {!collapsed && (
            <div className="px-2 pb-1.5 font-mono text-[11px] font-semibold tracking-[0.1em] text-text-muted">
              PROJECTS
            </div>
          )}
          <nav className={`flex flex-col gap-1 overflow-y-auto ${collapsed ? "items-center" : ""}`}>
            {projects === null && !collapsed && (
              <p className="px-2.5 py-2 text-[13px] text-text-faint">Loading…</p>
            )}
            {projects?.length === 0 && !collapsed && (
              <p className="px-2.5 py-2 text-[13px] text-text-faint">No projects yet.</p>
            )}
            {projects?.map((p) => {
              const active = p.id === activeProjectId;
              if (collapsed) {
                return (
                  <Link
                    key={p.id}
                    href={`/projects/${p.id}`}
                    title={`${p.name} · ${p.authority}`}
                    className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full font-mono text-[10px] font-semibold ${
                      active
                        ? "bg-accent text-white"
                        : "bg-panel text-text-secondary hover:bg-line"
                    }`}
                  >
                    {p.name.slice(0, 2).toUpperCase()}
                  </Link>
                );
              }
              return (
                <Link
                  key={p.id}
                  href={`/projects/${p.id}`}
                  className={`flex flex-col gap-0.5 rounded-[9px] px-2.5 py-[9px] ${
                    active
                      ? "border border-border-input bg-panel shadow-[0_1px_2px_rgba(0,0,0,.04)]"
                      : "hover:bg-line"
                  }`}
                >
                  <span
                    className={`truncate text-[13px] ${
                      active ? "font-medium text-ink" : "font-normal text-[#4E5054]"
                    }`}
                  >
                    {p.name}
                  </span>
                  <span
                    className={`font-mono text-[10px] font-medium ${
                      active ? "text-accent" : "text-text-faint"
                    }`}
                  >
                    {p.authority}
                  </span>
                </Link>
              );
            })}
          </nav>
          {collapsed ? (
            <button
              type="button"
              onClick={openNewProjectModal}
              title="New project"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-accent hover:bg-accent-wash"
            >
              <Plus size={15} strokeWidth={2.5} />
            </button>
          ) : (
            <button
              type="button"
              onClick={openNewProjectModal}
              className="flex items-center gap-2 rounded-[9px] px-2.5 py-[9px] text-left text-[13px] font-medium text-accent hover:bg-accent-wash"
            >
              + New project
            </button>
          )}
        </div>

        <div
          className={`mt-auto flex flex-col gap-0.5 border-t border-[#E4E4E1] pt-3 ${
            collapsed ? "items-center" : ""
          }`}
        >
          {isAdmin &&
            (collapsed ? (
              <Link
                href="/specs"
                title="Spec library (admin)"
                className="flex h-8 w-8 items-center justify-center rounded-full text-text-secondary hover:bg-line"
              >
                <BookOpen size={15} strokeWidth={2} />
              </Link>
            ) : (
              <Link
                href="/specs"
                className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] text-text-secondary hover:bg-line"
              >
                Spec library
                <span className="font-mono text-[10px] font-medium text-text-faint">ADMIN</span>
              </Link>
            ))}
          {collapsed ? (
            <>
              <div
                title={user?.email}
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-white"
              >
                {initials}
              </div>
              <button
                type="button"
                onClick={() => {
                  logout();
                  router.replace("/login");
                }}
                title="Sign out"
                className="flex h-8 w-8 items-center justify-center rounded-full text-text-secondary hover:bg-line"
              >
                <LogOut size={14} strokeWidth={2} />
              </button>
            </>
          ) : (
            <div className="flex items-center gap-2 px-2.5 py-2">
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-white">
                {initials}
              </div>
              <div className="min-w-0 flex-1 text-xs text-text-secondary">
                <span className="block truncate">{loading ? "…" : user?.email}</span>
                <span className="text-text-faint">
                  {user?.role === "tenant_admin"
                    ? "Tenant admin"
                    : user?.role === "super_admin"
                      ? "Super admin"
                      : "Reviewer"}
                </span>
              </div>
              <button
                type="button"
                onClick={() => {
                  logout();
                  router.replace("/login");
                }}
                className="shrink-0 text-xs text-accent"
              >
                Out
              </button>
            </div>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Mobile-only top bar — the persistent 236px sidebar doesn't fit small screens;
            per-page PageHeader back links carry navigation instead of a full bottom tab
            bar (a real simplification vs. README's 4-item mobile nav, not yet built). */}
        <div className="flex h-11 shrink-0 items-center justify-between border-b border-line-2 bg-sidebar px-4 md:hidden">
          <Link href="/projects" className="flex items-center gap-2">
            <div className="h-[18px] w-[18px] rounded-[5px] bg-accent" />
            <span className="text-[13px] font-semibold text-ink">Clause</span>
          </Link>
          <button
            type="button"
            onClick={() => {
              logout();
              router.replace("/login");
            }}
            className="text-xs text-accent"
          >
            Sign out
          </button>
        </div>
        <main className="flex flex-1 flex-col overflow-hidden bg-canvas">{children}</main>
      </div>
    </div>
  );
}
