"use client";

import { useState } from "react";
import { useLang } from "@/lib/LangContext";
import { CheckIcon } from "./primitives";

export default function InstallPill({ cmd }: { cmd: string }) {
  const { t } = useLang();
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      /* clipboard blocked — no-op */
    }
  };

  return (
    <button
      onClick={copy}
      className="grad-border group inline-flex items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3 text-left transition-colors hover:border-line-strong"
      aria-label="Copy install command"
    >
      <span className="select-none font-mono text-[14px] text-text-3">$</span>
      <span className="font-mono text-[14px] text-text-1">{cmd}</span>
      <span className="ml-1 inline-flex h-6 w-6 items-center justify-center rounded-md text-text-3 transition-colors group-hover:text-text-1">
        {copied ? (
          <CheckIcon className="text-accent" />
        ) : (
          <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
            <rect x="5.5" y="5.5" width="8" height="8" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <path d="M3.5 10.5h-.5A1.5 1.5 0 011.5 9V3A1.5 1.5 0 013 1.5h6A1.5 1.5 0 0110.5 3v.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        )}
      </span>
      <span
        className={`font-mono text-[11px] tracking-wide text-accent transition-opacity ${
          copied ? "opacity-100" : "opacity-0"
        }`}
      >
        {t.hero.copied}
      </span>
    </button>
  );
}
