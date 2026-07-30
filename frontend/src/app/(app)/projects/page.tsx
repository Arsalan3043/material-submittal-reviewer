"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { listProjects, type Project } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { Chip, Button } from "@/components/ui";
import { useNewProjectModal } from "@/components/new-project-modal";
import { useSession } from "@/lib/hooks/use-session";

/** README Screen 2 — project grid, or the create-project form directly when there are none. */
export default function ProjectsPage() {
  const router = useRouter();
  const { open: openNewProjectModal } = useNewProjectModal();
  const { user } = useSession();
  const [projects, setProjects] = useState<Project[] | null>(null);

  useEffect(() => {
    listProjects().then(setProjects);
  }, []);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Projects"
        right={
          <Button className="h-[34px] px-4" onClick={openNewProjectModal}>
            New project
          </Button>
        }
      />

      <div className="flex-1 overflow-y-auto p-6 md:p-6.5">
        {projects === null && (
          <p className="text-sm text-text-faint">Loading…</p>
        )}

        {projects?.length === 0 && (
          <div className="animate-riseIn flex h-full flex-col items-center justify-center gap-3 text-center">
            <p className="text-lg text-text-secondary">
              Create a project to start reviewing submittals
            </p>
            <Button className="h-10 px-5" onClick={openNewProjectModal}>
              Create your first project
            </Button>
          </div>
        )}

        {projects && projects.length > 0 && (
          <div className="animate-riseIn flex flex-col gap-4">
            <div className="flex items-baseline justify-between">
              <h1 className="text-[15px] font-semibold text-ink">Your projects</h1>
              <span className="text-[12.5px] text-text-muted">
                {projects.length} active{user ? ` · ${user.email.split("@")[1] ?? ""}` : ""}
              </span>
            </div>

            <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
              {projects.map((p) => (
                <button
                  key={p.id}
                  onClick={() => router.push(`/projects/${p.id}`)}
                  className="flex flex-col gap-2.5 rounded-xl border border-line bg-panel p-4 text-left transition-[border-color,transform] duration-200 hover:-translate-y-0.5 hover:border-accent"
                >
                  <div className="flex items-center justify-between">
                    <Chip className="bg-accent-wash text-accent">{p.authority}</Chip>
                  </div>
                  <div className="text-[15px] font-semibold leading-snug text-ink">
                    {p.name}
                  </div>
                  <div className="text-[12.5px] text-text-muted">
                    Created {new Date(p.created_at).toLocaleDateString()}
                  </div>
                </button>
              ))}

              <button
                onClick={openNewProjectModal}
                className="flex flex-col justify-center gap-1.5 rounded-xl border-[1.5px] border-dashed border-[#D8D7D2] p-4 text-left hover:border-accent hover:bg-[#F7F8FF]"
              >
                <span className="text-sm font-semibold text-accent">+ New project</span>
                <span className="text-xs text-text-muted">Name and authority — two fields.</span>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
