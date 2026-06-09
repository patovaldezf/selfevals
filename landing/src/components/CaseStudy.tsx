"use client";

import { useLang } from "@/lib/LangContext";
import { Eyebrow, Reveal } from "./primitives";

export default function CaseStudy() {
  const { t } = useLang();

  return (
    <section id="case" className="relative overflow-hidden py-24 sm:py-32">
      <div className="glow right-[-100px] top-20 h-[360px] w-[420px] bg-accent/10" />
      <div className="mx-auto max-w-[1180px] px-5 sm:px-8">
        <div className="grid gap-12 lg:grid-cols-[1.1fr_0.9fr] lg:gap-16">
          <Reveal>
            <div>
              <Eyebrow>{t.caseStudy.eyebrow}</Eyebrow>
              <h2 className="display-2 mt-5 text-[clamp(1.8rem,3.8vw,2.7rem)]">
                {t.caseStudy.title}
              </h2>
              <p className="mt-5 text-[16px] leading-relaxed text-text-2">
                {t.caseStudy.body}
              </p>
              <p className="mt-4 text-[16px] leading-relaxed text-text-2">
                {t.caseStudy.body2}
              </p>
            </div>
          </Reveal>

          <Reveal delay={100}>
            <div className="grid grid-cols-2 gap-3 self-center">
              {t.caseStudy.stats.map((s) => (
                <div
                  key={s.l}
                  className="grad-border rounded-xl border border-line bg-surface p-5"
                >
                  <div className="font-mono text-[30px] font-medium tracking-tight text-accent tnum">
                    {s.v}
                  </div>
                  <div className="mt-1.5 text-[13px] leading-snug text-text-2">
                    {s.l}
                  </div>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}
