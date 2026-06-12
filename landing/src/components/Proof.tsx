"use client";

import { useLang } from "@/lib/LangContext";
import { Reveal } from "./primitives";

export default function Proof() {
  const { t } = useLang();

  return (
    <section className="relative py-24 sm:py-28">
      <div className="mx-auto max-w-[820px] px-5 text-center sm:px-8">
        <Reveal>
          <p className="font-mono text-[12px] uppercase tracking-[0.14em] text-text-3">
            {t.proof.eyebrow}
          </p>
          <blockquote className="display-2 mx-auto mt-7 max-w-[680px] text-[clamp(1.4rem,3vw,2rem)] leading-[1.3] text-text-1">
            <span className="text-accent">“</span>
            {t.proof.quote}
            <span className="text-accent">”</span>
          </blockquote>
          <div className="mt-7 flex items-center justify-center gap-3">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-line-strong bg-surface font-mono text-[13px] text-accent">
              bo
            </span>
            <div className="text-left">
              <div className="text-[14px] font-semibold text-text-1">
                {t.proof.author}
              </div>
              <div className="text-[13px] text-text-3">{t.proof.role}</div>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}
