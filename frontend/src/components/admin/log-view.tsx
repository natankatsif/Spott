"use client";

import { useEffect, useRef } from "react";
import { CopyButton } from "@/components/spell/copy-button";
import { cn } from "@/lib/utils";

/** Tail of a job log: monospace, stays scrolled to the newest line unless the reader scrolled up. */
export function LogView({ lines, title, className }: { lines: string[]; title: string; className?: string }) {
  const ref = useRef<HTMLPreElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div className={cn("overflow-hidden rounded-xl border bg-[#0f1729] text-[#d6deeb]", className)}>
      <div className="flex items-center justify-between border-white/10 border-b px-3 py-1.5">
        <span className="font-medium text-[11px] text-white/60 uppercase tracking-wider">{title}</span>
        <CopyButton className="text-white/60 hover:bg-white/10 hover:text-white" size="sm" value={lines.join("\n")} />
      </div>
      <pre
        className="max-h-64 overflow-auto px-3 py-2 font-mono text-[12px] leading-5"
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
        }}
        ref={ref}
      >
        {lines.length ? (
          lines.map((line, i) => (
            <div className={cn("whitespace-pre-wrap break-all", /error|failed|traceback/i.test(line) && "text-[#ff8f8f]")} key={i}>
              {line}
            </div>
          ))
        ) : (
          <span className="text-white/40">…</span>
        )}
      </pre>
    </div>
  );
}
