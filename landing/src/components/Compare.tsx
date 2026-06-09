"use client";

import { useLang } from "@/lib/LangContext";
import { Eyebrow, Reveal, CheckIcon, DashIcon } from "./primitives";

function Cell({ v }: { v: boolean | string }) {
  if (v === true)
    return (
      <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-accent/12 text-accent">
        <CheckIcon />
      </span>
    );
  if (v === "partial")
    return (
      <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-[#f5b14b]/12 text-[#f5b14b]">
        <DashIcon />
      </span>
    );
  return (
    <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-surface-2 text-text-3">
      <DashIcon />
    </span>
  );
}

export default function Compare() {
  const { t } = useLang();

  return (
    <section className="relative border-y border-line bg-bg-2/40 py-24 sm:py-32">
      <div className="mx-auto max-w-[920px] px-5 sm:px-8">
        <Reveal>
          <div className="text-center">
            <Eyebrow>{t.compare.eyebrow}</Eyebrow>
            <h2 className="display-2 mt-5 text-[clamp(1.8rem,3.8vw,2.7rem)]">
              {t.compare.title}
            </h2>
            <p className="mx-auto mt-4 max-w-[560px] text-[17px] leading-relaxed text-text-2">
              {t.compare.sub}
            </p>
          </div>
        </Reveal>

        <Reveal delay={100}>
          <div className="mt-12 overflow-hidden rounded-xl border border-line bg-surface">
            <div className="grid grid-cols-[1fr_auto_auto] items-center gap-x-6 border-b border-line px-5 py-4 sm:px-7">
              <span className="text-[13px] uppercase tracking-wide text-text-3" />
              <span className="w-24 text-center text-[13px] font-semibold text-accent sm:w-28">
                {t.compare.seHead}
              </span>
              <span className="w-24 text-center text-[12px] leading-tight text-text-3 sm:w-28">
                {t.compare.otherHead}
              </span>
            </div>
            {t.compare.rows.map((row, i) => (
              <div
                key={i}
                className="grid grid-cols-[1fr_auto_auto] items-center gap-x-6 px-5 py-4 sm:px-7 [&:not(:last-child)]:border-b [&:not(:last-child)]:border-line"
              >
                <span className="text-[14.5px] text-text-1">{row.f}</span>
                <span className="flex w-24 justify-center sm:w-28">
                  <Cell v={row.se} />
                </span>
                <span className="flex w-24 justify-center sm:w-28">
                  <Cell v={row.other} />
                </span>
              </div>
            ))}
          </div>
        </Reveal>
      </div>
    </section>
  );
}
