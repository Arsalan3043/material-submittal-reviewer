"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useNewProjectModal } from "@/components/new-project-modal";

/**
 * Create Project is a modal now (README Screen 3), not a route — this page exists only so
 * old links/bookmarks to /projects/new still do something sensible: open the modal on top
 * of the projects grid instead of 404ing.
 */
export default function NewProjectRedirectPage() {
  const router = useRouter();
  const { open } = useNewProjectModal();

  useEffect(() => {
    router.replace("/projects");
    open();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return null;
}
