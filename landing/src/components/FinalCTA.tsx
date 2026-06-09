"use client";

import { useLang } from "@/lib/LangContext";
import { APP_URL } from "@/lib/i18n";
import { Reveal, ArrowIcon, GitHubIcon } from "./primitives";
import InstallPill from "./InstallPill";

export default function FinalCTA() {
  const { t } = useLang();

  return (
    <section className="relative overflow-hidden py-28 sm:py-36">
      <div className="glow left-1/2 top-1/2 h-[420px] w-[680px] -translate-x-1/2 -translate-y-1/2 bg-accent/20" />
      <div className="grid-bg pointer-events-none absolute inset-0 opacity-60" />

      <div className="relative mx-auto max-w-[760px] px-5 text-center sm:px-8">
        <Reveal>
          <h2 className="display mx-auto max-w-[620px] text-[clamp(2.1rem,5vw,3.4rem)]">
            {t.cta.title}
          </h2>
          <p className="mx-auto mt-5 max-w-[520px] text-[17px] leading-relaxed text-text-2">
            {t.cta.sub}
          </p>

          <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <a
              href={`${APP_URL}/signup`}
              className="grad-border inline-flex items-center gap-2 rounded-xl bg-accent px-6 py-3.5 text-[15px] font-semibold text-accent-fg transition-transform duration-150 hover:scale-[1.02]"
            >
              {t.cta.primary}
              <ArrowIcon />
            </a>
            <a
              href="https://github.com/patovaldezf/selfevals"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-xl border border-line bg-surface px-6 py-3.5 text-[15px] font-medium text-text-1 transition-colors hover:border-line-strong"
            >
              <GitHubIcon />
              {t.cta.secondary}
            </a>
          </div>

          <div className="mt-8 flex justify-center">
            <InstallPill cmd={t.cta.install} />
          </div>
        </Reveal>
      </div>
    </section>
  );
}
