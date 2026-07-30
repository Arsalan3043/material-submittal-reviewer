"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUp } from "lucide-react";
import { askChat, getChatHistory, type ChatTurn } from "@/lib/api";

const SOURCE_LABEL: Record<string, string> = {
  spec_rag: "FROM SPEC LIBRARY",
  submittal_rag: "FROM YOUR DOCUMENTS",
  report_json: "FROM REVIEW FINDINGS",
};

const SUGGESTIONS = ["Why this recommendation?", "What is still missing?", "Summarize the critical findings"];

/** README Screen 7/8 right rail — grounded Q&A, every answer showing its source. */
export function ChatPanel({ submittalId }: { submittalId: string }) {
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getChatHistory(submittalId).then(setHistory);
  }, [submittalId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, busy]);

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    setHistory((prev) => [
      ...prev,
      { question: q, answer: "", route: "", sources: [], created_at: new Date().toISOString() },
    ]);
    try {
      const res = await askChat(submittalId, q);
      setHistory((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          question: q,
          answer: res.answer,
          route: res.source,
          sources: [res.confidence],
          created_at: new Date().toISOString(),
        };
        return next;
      });
    } catch {
      setHistory((prev) => {
        const next = [...prev];
        next[next.length - 1] = {
          question: q,
          answer: "Couldn't reach the assistant — try again.",
          route: "",
          sources: [],
          created_at: new Date().toISOString(),
        };
        return next;
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 px-4.5 pb-2 pt-3.5 font-mono text-[11px] font-semibold tracking-[0.08em] text-text-muted">
        ASK ABOUT THIS SUBMITTAL
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-4.5 pb-3">
        {history.length === 0 && (
          <p className="text-[12.5px] leading-relaxed text-text-faint">
            Ask a question grounded in this submittal and its spec, e.g. &ldquo;what does the
            contractor need to fix?&rdquo;
          </p>
        )}
        {history.map((turn, i) => {
          const isLastAndBusy = busy && i === history.length - 1;
          return (
            <div key={i} className="flex flex-col gap-1.5">
              <div className="max-w-[92%] self-end rounded-[10px_10px_3px_10px] bg-ink px-3.5 py-2.5 text-[12.5px] leading-relaxed text-white">
                {turn.question}
              </div>
              {isLastAndBusy ? (
                <div className="flex w-[52px] gap-[5px] rounded-[10px_10px_10px_3px] border border-[#E9E9E6] bg-panel px-3.5 py-3">
                  {[0, 0.2, 0.4].map((delay) => (
                    <span
                      key={delay}
                      className="animate-softPulse h-1.5 w-1.5 rounded-full bg-[#B0B0B5]"
                      style={{ animationDelay: `${delay}s` }}
                    />
                  ))}
                </div>
              ) : (
                turn.answer && (
                  <div className="flex flex-col gap-1.5">
                    <div className="rounded-[10px_10px_10px_3px] border border-[#E9E9E6] bg-panel px-3.5 py-3 text-[12.5px] leading-relaxed text-ink-2">
                      {turn.answer}
                    </div>
                    {turn.route && (
                      <div className="flex items-center gap-1.5">
                        <span className="inline-flex h-5 items-center rounded-[5px] bg-accent-wash px-1.5 font-mono text-[9px] font-semibold text-accent">
                          {SOURCE_LABEL[turn.route] ?? turn.route.toUpperCase()}
                        </span>
                        {turn.sources[0] && (
                          <span className="text-[11px] text-text-muted">
                            {turn.sources[0]} confidence
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                )
              )}
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>
      <div className="flex shrink-0 flex-col gap-2 px-4.5 pb-3.5">
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => send(s)}
              className="h-[26px] rounded-[13px] border border-[#E2E2DF] bg-panel px-2.5 text-[11.5px] text-text-secondary hover:border-accent hover:text-accent"
            >
              {s}
            </button>
          ))}
        </div>
        <div className="flex h-[38px] items-center gap-1.5 rounded-[10px] border border-[#E2E2DF] bg-panel pl-3 pr-1.5">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") send(input);
            }}
            placeholder="Ask a follow-up…"
            className="min-w-0 flex-1 border-none bg-transparent text-[12.5px] text-ink outline-none placeholder:text-text-faint"
          />
          <button
            onClick={() => send(input)}
            className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-lg bg-accent text-white"
          >
            <ArrowUp size={13} strokeWidth={2.5} />
          </button>
        </div>
      </div>
    </div>
  );
}
