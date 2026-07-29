"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { getStoredTokens, logout } from "@/lib/auth";
import { listProjects, type Project } from "@/lib/api";

/**
 * Sidebar shell for every authenticated screen (UX spec Screen 2's left sidebar, shared
 * across all project/submittal pages so navigation doesn't reset). Auth guard lives here:
 * every route under (app)/ requires a token, checked client-side since tokens live in
 * localStorage, not a cookie the server could check.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  useEffect(() => {
    if (!getStoredTokens()) {
      router.replace("/login");
      return;
    }
    setAuthChecked(true);
  }, [router]);

  useEffect(() => {
    if (!authChecked) return;
    listProjects()
      .then(setProjects)
      .catch(() => setProjects([]));
  }, [authChecked, pathname]);

  if (!authChecked) return null;

  return (
    <div className="flex flex-1">
      <aside className="flex w-64 shrink-0 flex-col border-r border-zinc-200 bg-white">
        <div className="border-b border-zinc-200 p-4">
          <Link href="/projects/new">
            <button className="w-full rounded-md bg-zinc-900 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-800">
              + New Project
            </button>
          </Link>
        </div>

        <nav className="flex-1 overflow-y-auto p-2">
          {projects === null && (
            <p className="px-2 py-2 text-sm text-zinc-400">Loading projects...</p>
          )}
          {projects?.length === 0 && (
            <p className="px-2 py-2 text-sm text-zinc-400">No projects yet.</p>
          )}
          {projects?.map((p) => (
            <Link
              key={p.id}
              href={`/projects/${p.id}`}
              className={`block rounded-md px-3 py-2 text-sm hover:bg-zinc-100 ${
                pathname === `/projects/${p.id}` ? "bg-zinc-100 font-medium" : "text-zinc-700"
              }`}
            >
              <div className="truncate">{p.name}</div>
              <span className="inline-block rounded bg-zinc-200 px-1.5 py-0.5 text-[11px] text-zinc-600">
                {p.authority}
              </span>
            </Link>
          ))}
        </nav>

        <div className="border-t border-zinc-200 p-2">
          <button
            onClick={() => {
              logout();
              router.replace("/login");
            }}
            className="w-full rounded-md px-3 py-2 text-left text-sm text-zinc-600 hover:bg-zinc-100"
          >
            Sign out
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto bg-zinc-50">{children}</main>
    </div>
  );
}
