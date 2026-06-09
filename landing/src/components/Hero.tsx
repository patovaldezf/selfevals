"use client";

import { useLang } from "@/lib/LangContext";
import { APP_URL } from "@/lib/i18n";
import { Eyebrow, ArrowIcon } from "./primitives";
import InstallPill from "./InstallPill";
import Terminal from "./Terminal";

export default function Hero() {
  const { t } = useLang();

  return (
    <section id="top" className="relative overflow-hidden pt-32 pb-20 sm:pt-40 sm:pb-28">
      {/* ambient glow + grid */}
      <div className="glow left-1/2 top-[-120px] h-[440px] w-[640px] -translate-x-1/2 bg-accent/30" />
      <div className="grid-bg pointer-events-none absolute inset-0" />

      <div className="relative mx-auto max-w-[1180px] px-5 sm:px-8">
        <div className="mx-auto max-w-[820px] text-center">
          <div className="flex justify-center">
            <Eyebrow>
              <span className="h-1.5 w-1.5 rounded-full bg-accent" />
              {t.hero.badge}
            </Eyebrow>
          </div>

          <h1 className="display mt-7 text-[clamp(2.4rem,6vw,4.4rem)] text-text-1">
            {t.hero.title1}
            <br className="hidden sm:block" />{" "}
            <span className="bg-gradient-to-b from-text-1 to-text-2 bg-clip-text text-transparent">
              {t.hero.title2}
            </span>
          </h1>

          <p className="mx-auto mt-6 max-w-[640px] text-[17px] leading-relaxed text-text-2 sm:text-[18px]">
            {t.hero.sub}
          </p>

          <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <a
              href={`${APP_URL}/signup`}
              className="grad-border inline-flex items-center gap-2 rounded-xl bg-accent px-5 py-3 text-[15px] font-semibold text-accent-fg transition-transform duration-150 hover:scale-[1.02]"
            >
              {t.hero.ctaPrimary}
              <ArrowIcon />
            </a>
            <a
              href="https://github.com/patovaldezf/selfevals#readme"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-2 rounded-xl border border-line bg-surface px-5 py-3 text-[15px] font-medium text-text-1 transition-colors hover:border-line-strong"
            >
              {t.hero.ctaSecondary}
            </a>
          </div>

          <div className="mt-7 flex justify-center">
            <InstallPill cmd={t.hero.install} />
          </div>
        </div>

        {/* terminal */}
        <div className="relative mx-auto mt-16 max-w-[860px]">
          <div className="glow left-1/2 top-10 h-[300px] w-[80%] -translate-x-1/2 bg-accent/12" />
          <div className="relative">
            <Terminal />
          </div>
        </div>
      </div>
    </section>
  );
}
