"use client";

/**
 * The 62px header bar repeated on every authenticated screen (README §2's "Header"). Each
 * page supplies title/authority/back target; the running-review pill renders itself from
 * shared context so pages don't have to wire it individually.
 */
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Chip } from "@/components/ui";
import { useRunningReview } from "@/lib/hooks/use-running-review";

export function PageHeader({
  title,
  authority,
  backHref,
  right,
}: {
  title: string;
  authority?: string;
  backHref?: string;
  right?: React.ReactNode;
}) {
  const { runningReview } = useRunningReview();

  return (
    <div className="flex h-[62px] shrink-0 items-center justify-between gap-4 border-b border-line-2 px-6">
      <div className="flex min-w-0 items-center gap-2.5">
        {backHref && (
          <Link
            href={backHref}
            className="flex h-7 shrink-0 items-center gap-1 rounded-lg border border-line-2 bg-panel px-2.5 text-xs font-medium text-text-secondary hover:bg-[#F4F4F2]"
          >
            <ArrowLeft size={13} strokeWidth={2.5} />
            Back
          </Link>
        )}
        <span className="truncate text-base font-semibold tracking-[-0.01em] text-ink">
          {title}
        </span>
        {authority && (
          <Chip className="bg-accent-wash text-accent">{authority}</Chip>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2.5">
        {runningReview && (
          <Link
            href={`/submittals/${runningReview.submittalId}`}
            className="flex h-8 items-center gap-2 rounded-[9px] bg-accent-wash px-3"
          >
            <span className="animate-softPulse h-2 w-2 rounded-full bg-accent" />
            <span className="text-xs font-medium text-accent">
              Review running · {runningReview.percent}%
            </span>
          </Link>
        )}
        {right}
      </div>
    </div>
  );
}
