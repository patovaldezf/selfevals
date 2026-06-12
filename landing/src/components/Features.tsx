"use client";

import { useLang } from "@/lib/LangContext";
import { Eyebrow, Reveal } from "./primitives";

function SectionHead({
  eyebrow,
  title,
  sub,
}: {
  eyebrow: string;
  title: string;
  sub: string;
}) {
  return (
    <div className="mx-auto max-w-[680px] text-center">
      <Eyebrow>{eyebrow}</Eyebrow>
      <h2 className="display-2 mt-5 text-[clamp(1.9rem,4vw,2.9rem)]">{title}</h2>
      <p className="mt-4 text-[17px] leading-relaxed text-text-2">{sub}</p>
    </div>
  );
}

export default function Features() {
  const { t } = useLang();

  return (
    <section id="features" className="relative py-24 sm:py-32">
      <div className="mx-auto max-w-[1180px] px-5 sm:px-8">
        <Reveal>
          <SectionHead
            eyebrow={t.features.eyebrow}
            title={t.features.title}
            sub={t.features.sub}
          />
        </Reveal>

        <div className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {t.features.items.map((item, i) => (
            <Reveal key={item.tag} delay={(i % 3) * 70}>
              <article className="group grad-border relative h-full overflow-hidden rounded-xl border border-line bg-surface p-6 transition-colors hover:border-line-strong">
                <div className="glow -right-10 -top-16 h-32 w-32 bg-accent/0 transition-all duration-500 group-hover:bg-accent/20" />
                <span className="relative inline-flex rounded-md border border-line-strong bg-bg-2 px-2 py-1 font-mono text-[11px] uppercase tracking-wide text-accent">
                  {item.tag}
                </span>
                <h3 className="relative mt-4 text-[18px] font-semibold tracking-tight text-text-1">
                  {item.title}
                </h3>
                <p className="relative mt-2 text-[14.5px] leading-relaxed text-text-2">
                  {item.body}
                </p>
              </article>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
