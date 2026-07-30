"use client";

/**
 * Screen 3 (README) — a modal, not a route: "Two fields. Attach spec libraries once you're
 * inside." Triggered from the sidebar ("+ New project"), the projects grid's dashed tile,
 * and the empty state — all on different pages, hence a context provider mounted once at
 * the (app) layout level rather than local state per page.
 */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type FormEvent,
} from "react";
import { useRouter } from "next/navigation";
import { createProject } from "@/lib/api";
import { Button, MonoLabel, TextInput } from "@/components/ui";
import { useToast } from "@/components/toast";

interface NewProjectModalContextValue {
  open: () => void;
}

const NewProjectModalContext = createContext<NewProjectModalContextValue | null>(null);

export function useNewProjectModal(): NewProjectModalContextValue {
  const ctx = useContext(NewProjectModalContext);
  if (!ctx) throw new Error("useNewProjectModal must be used within NewProjectModalProvider");
  return ctx;
}

export function NewProjectModalProvider({
  children,
  onCreated,
}: {
  children: React.ReactNode;
  onCreated?: () => void;
}) {
  const router = useRouter();
  const { showToast } = useToast();
  const [isOpen, setIsOpen] = useState(false);
  const [name, setName] = useState("");
  const [authority, setAuthority] = useState<"ADM" | "TAQA">("ADM");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function open() {
    setName("");
    setAuthority("ADM");
    setError(null);
    setIsOpen(true);
  }

  function close() {
    if (submitting) return;
    setIsOpen(false);
  }

  useEffect(() => {
    if (!isOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") close();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (name.trim().length < 3) {
      setError("Give the project a name you will recognise in the register.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const project = await createProject({ name: name.trim(), authority });
      setIsOpen(false);
      showToast("Project created — attach a spec library next");
      onCreated?.();
      router.push(`/projects/${project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create project");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <NewProjectModalContext.Provider value={{ open }}>
      {children}
      {isOpen && (
        <div
          className="animate-fadeIn fixed inset-0 z-50 flex items-center justify-center bg-[rgba(20,20,24,.42)] p-4"
          onClick={close}
        >
          <div
            className="animate-slideUp w-full max-w-[420px] rounded-[14px] bg-panel p-[22px] shadow-[0_24px_60px_-20px_rgba(0,0,0,.4)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3.5">
              <h2 className="text-[17px] font-semibold text-ink">New project</h2>
              <p className="mt-1.5 text-[12.5px] text-[#7C7F84]">
                Two fields. Attach spec libraries once you&rsquo;re inside.
              </p>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-col gap-3.5">
              <div className="flex flex-col gap-1.5">
                <MonoLabel htmlFor="project-name">PROJECT NAME</MonoLabel>
                <TextInput
                  id="project-name"
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Saadiyat Marina — Utilities P1"
                  className="h-10"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <MonoLabel>AUTHORITY</MonoLabel>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setAuthority("ADM")}
                    className={`h-10 flex-1 rounded-[8px] text-[12.5px] font-semibold transition-colors ${
                      authority === "ADM"
                        ? "bg-ink text-white"
                        : "border border-line-2 bg-panel text-text-secondary"
                    }`}
                  >
                    ADM · Abu Dhabi Municipality
                  </button>
                  <button
                    type="button"
                    onClick={() => setAuthority("TAQA")}
                    className={`h-10 w-24 rounded-[8px] text-[12.5px] font-semibold transition-colors ${
                      authority === "TAQA"
                        ? "bg-ink text-white"
                        : "border border-line-2 bg-panel text-text-secondary"
                    }`}
                  >
                    TAQA
                  </button>
                </div>
                <p className="text-[11.5px] leading-tight text-text-faint">
                  Set once at creation — it decides which clause library and whether AVL
                  checks run.
                </p>
              </div>

              {error && (
                <div className="animate-popIn rounded-[8px] bg-critical-bg px-[11px] py-2 text-xs text-critical-text">
                  {error}
                </div>
              )}

              <div className="mt-0.5 flex justify-end gap-2">
                <Button variant="ghost" className="h-[38px] px-[15px]" onClick={close}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  className="h-[38px] px-[17px]"
                  disabled={submitting}
                >
                  {submitting ? "Creating..." : "Create project"}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </NewProjectModalContext.Provider>
  );
}
