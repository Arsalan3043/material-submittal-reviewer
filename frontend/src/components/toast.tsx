"use client";

/**
 * Toast system per README §Interactions: bottom-center on laptop, full-width above the
 * mobile tab bar, dark pill with a status dot, optional action label, auto-dismiss ~4.2s.
 */
import { createContext, useCallback, useContext, useRef, useState } from "react";

interface ToastState {
  text: string;
  actionLabel?: string;
  onAction?: () => void;
}

interface ToastContextValue {
  showToast: (text: string, opts?: { actionLabel?: string; onAction?: () => void }) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toast, setToast] = useState<ToastState | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback<ToastContextValue["showToast"]>((text, opts) => {
    if (timer.current) clearTimeout(timer.current);
    setToast({ text, actionLabel: opts?.actionLabel, onAction: opts?.onAction });
    timer.current = setTimeout(() => setToast(null), 4200);
  }, []);

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      {toast && (
        <div
          className="animate-slideUp fixed bottom-[86px] left-4 right-4 z-50 flex items-center gap-2.5 rounded-xl bg-toast-bg px-4 py-3 shadow-[0_12px_30px_-12px_rgba(0,0,0,.5)] md:bottom-[22px] md:left-1/2 md:right-auto md:-translate-x-1/2 md:rounded-[11px]"
        >
          <span className="h-[7px] w-[7px] shrink-0 rounded-full bg-toast-dot" />
          <span className="flex-1 text-[13px] font-medium leading-snug text-[#FAFAF9]">
            {toast.text}
          </span>
          {toast.actionLabel && (
            <button
              type="button"
              onClick={() => {
                toast.onAction?.();
                setToast(null);
              }}
              className="shrink-0 text-[13px] font-semibold text-toast-action"
            >
              {toast.actionLabel}
            </button>
          )}
        </div>
      )}
    </ToastContext.Provider>
  );
}
