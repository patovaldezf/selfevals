"use client";

import { useLang } from "@/lib/LangContext";
import { Eyebrow, Reveal } from "./primitives";

const SNIPPET = [
  { p: "$", c: "selfevals examples copy pingpong", note: "01" },
  { p: "$", c: "selfevals run evals/experiments/example_pingpong.yaml", note: "02" },
  { p: "$", c: "selfevals report <ws> <exp>", note: "03" },
];

export default function HowItWorks() {
  const { t } = useLang();

  return (
    <section id="how" className="relative border-y border-line bg-bg-2/40 py-24 sm:py-32">
      <div className="mx-auto max-w-[1180px] px-5 sm:px-8">
        <div className="grid items-center gap-14 lg:grid-cols-2">
          <Reveal>
            <div>
              <Eyebrow>{t.how.eyebrow}</Eyebrow>
              <h2 className="display-2 mt-5 text-[clamp(1.9rem,4vw,2.8rem)]">
                {t.how.title}
              </h2>
              <p className="mt-4 max-w-[480px] text-[17px] leading-relaxed text-text-2">
                {t.how.sub}
              </p>

              <ol className="mt-9 space-y-5">
                {t.how.steps.map((s) => (
                  <li key={s.n} className="flex gap-4">
                    <span className="mt-0.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line-strong bg-surface font-mono text-[13px] text-accent">
                      {s.n}
                    </span>
                    <div>
                      <div className="text-[15.5px] font-semibold text-text-1">
                        {s.t}
                      </div>
                      <div className="mt-0.5 text-[14px] leading-relaxed text-text-2">
                        {s.d}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </Reveal>

          <Reveal delay={100}>
            <div className="grad-border rounded-xl bg-[#0a0c0d] shadow-card">
              <div className="flex items-center gap-2 border-b border-line px-4 py-3">
                <span className="font-mono text-[12px] text-text-3">
                  ~/your-agent
                </span>
              </div>
              <div className="space-y-4 px-5 py-6 font-mono text-[13.5px] leading-relaxed">
                {SNIPPET.map((l) => (
                  <div key={l.note} className="flex items-start gap-3">
                    <span className="select-none pt-px text-[11px] text-text-3">
                      {l.note}
                    </span>
                    <span className="text-text-3">{l.p}</span>
                    <span className="text-text-1">{l.c}</span>
                  </div>
                ))}
                <div className="!mt-6 rounded-lg border border-line bg-bg-2/60 px-4 py-3 text-[12.5px] text-text-3">
                  <span className="text-accent">→</span> markdown report ·
                  iterations ranked · winner selected{" "}
                  <span className="text-text-2">
                    — <span className="tnum">&lt;1s</span>, no API key
                  </span>
                </div>
              </div>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
