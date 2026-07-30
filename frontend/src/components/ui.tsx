/**
 * Small shared primitives used across every screen — kept deliberately minimal (no new
 * component-library dependency; README says map onto an existing kit if present, and none
 * was, so these are the kit). Each one exists because 3+ screens repeat the same pattern.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Chip({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex h-5 items-center rounded-[5px] px-[7px] font-mono text-[10px] font-semibold tracking-[0.06em] ${className}`}
    >
      {children}
    </span>
  );
}

export function StatusChip({
  label,
  severityClass,
  className = "",
}: {
  label: string;
  severityClass: string;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex h-[22px] items-center rounded-[6px] px-[9px] font-mono text-[10px] font-semibold ${severityClass} ${className}`}
    >
      {label}
    </span>
  );
}

type ButtonVariant = "primary" | "dark" | "ghost" | "danger-outline";

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-white shadow-[0_2px_6px_rgba(27,77,255,.3)] hover:bg-accent-hover disabled:bg-[#E6E6E3] disabled:text-text-faint disabled:shadow-none disabled:cursor-not-allowed",
  dark: "bg-ink text-white hover:bg-black",
  ghost: "bg-panel border border-line-2 text-text-secondary hover:bg-[#F4F4F2]",
  "danger-outline": "bg-panel border border-[#F2CFCF] text-critical-text hover:bg-critical-bg",
};

export function Button({
  variant = "primary",
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center rounded-[9px] text-[13px] font-semibold transition-colors duration-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${VARIANT_CLASS[variant]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function MonoLabel({
  children,
  className = "",
  ...props
}: React.LabelHTMLAttributes<HTMLLabelElement> & { children: ReactNode }) {
  return (
    <label
      className={`font-mono text-[11px] font-medium uppercase tracking-[0.07em] text-text-muted ${className}`}
      {...props}
    >
      {children}
    </label>
  );
}

export function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  const { className = "", ...rest } = props;
  return (
    <input
      className={`h-[42px] rounded-[10px] border border-border-input bg-panel px-[13px] text-sm text-ink placeholder:text-text-faint focus:border-accent focus:outline-none ${className}`}
      {...rest}
    />
  );
}
