"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { listProjects, type Project } from "@/lib/api";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[] | null>(null);

  useEffect(() => {
    listProjects().then(setProjects);
  }, []);

  if (projects === null) {
    return <div className="p-8 text-sm text-zinc-400">Loading...</div>;
  }

  if (projects.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
        <p className="text-lg text-zinc-600">
          Create a project to start reviewing submittals
        </p>
        <Link href="/projects/new">
          <button className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800">
            Create your first project
          </button>
        </Link>
      </div>
    );
  }

  return (
    <div className="p-8">
      <h1 className="mb-6 text-lg font-semibold text-zinc-900">Projects</h1>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {projects.map((p) => (
          <Link
            key={p.id}
            href={`/projects/${p.id}`}
            className="rounded-lg border border-zinc-200 bg-white p-4 hover:border-zinc-300 hover:shadow-sm"
          >
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-medium text-zinc-900">{p.name}</h2>
              <span className="rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600">
                {p.authority}
              </span>
            </div>
            {p.description && (
              <p className="line-clamp-2 text-sm text-zinc-500">{p.description}</p>
            )}
            <p className="mt-3 text-xs text-zinc-400">
              Created {new Date(p.created_at).toLocaleDateString()}
            </p>
          </Link>
        ))}
      </div>
    </div>
  );
}
