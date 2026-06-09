"use client";

import { useEffect, useState } from "react";
import { useLang } from "@/lib/LangContext";
import { APP_URL } from "@/lib/i18n";
import { Logo } from "./primitives";

const GITHUB = "https://github.com/patovaldezf/selfevals";

export default function Nav() {
  const { t, lang, setLang } = useLang();
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const links = [
    { href: "#features", label: t.nav.product },
    { href: "#how", label: t.nav.howItWorks },
    { href: "#case", label: t.nav.caseStudy },
    { href: GITHUB, label: t.nav.docs, external: true },
  ];

  return (
    <header
      className={`fixed inset-x-0 top-0 z-50 transition-colors duration-300 ${
        scrolled
          ? "border-b border-line bg-bg/80 backdrop-blur-xl"
          : "border-b border-transparent"
      }`}
    >
      <nav className="mx-auto flex h-16 max-w-[1180px] items-center justify-between px-5 sm:px-8">
        <a href="#top" aria-label="selfevals" className="shrink-0">
          <Logo />
        </a>

        <div className="hidden items-center gap-7 md:flex">
          {links.map((l) => (
            <a
              key={l.label}
              href={l.href}
              target={l.external ? "_blank" : undefined}
              rel={l.external ? "noreferrer" : undefined}
              className="text-[14px] text-text-2 transition-colors hover:text-text-1"
            >
              {l.label}
            </a>
          ))}
        </div>

        <div className="flex items-center gap-2 sm:gap-3">
          <LangToggle lang={lang} setLang={setLang} />

          <a
            href={`${APP_URL}/login`}
            className="hidden rounded-lg px-3 py-1.5 text-[14px] text-text-2 transition-colors hover:text-text-1 sm:inline-flex"
          >
            {t.nav.login}
          </a>
          <a
            href={`${APP_URL}/signup`}
            className="grad-border inline-flex items-center rounded-lg bg-text-1 px-3.5 py-1.5 text-[14px] font-medium text-bg transition-transform duration-150 hover:scale-[1.02]"
          >
            {t.nav.signup}
          </a>

          <button
            aria-label="Menu"
            onClick={() => setOpen((v) => !v)}
            className="ml-1 inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line text-text-2 md:hidden"
          >
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              {open ? (
                <path d="M3 3l10 10M13 3L3 13" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              ) : (
                <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              )}
            </svg>
          </button>
        </div>
      </nav>

      {open && (
        <div className="border-t border-line bg-bg/95 backdrop-blur-xl md:hidden">
          <div className="flex flex-col gap-1 px-5 py-4">
            {links.map((l) => (
              <a
                key={l.label}
                href={l.href}
                target={l.external ? "_blank" : undefined}
                rel={l.external ? "noreferrer" : undefined}
                onClick={() => setOpen(false)}
                className="rounded-lg px-2 py-2.5 text-[15px] text-text-2 hover:bg-surface hover:text-text-1"
              >
                {l.label}
              </a>
            ))}
            <a
              href={`${APP_URL}/login`}
              className="rounded-lg px-2 py-2.5 text-[15px] text-text-2 hover:bg-surface hover:text-text-1"
            >
              {t.nav.login}
            </a>
          </div>
        </div>
      )}
    </header>
  );
}

function LangToggle({
  lang,
  setLang,
}: {
  lang: "en" | "es";
  setLang: (l: "en" | "es") => void;
}) {
  return (
    <div className="flex items-center rounded-lg border border-line bg-surface p-0.5 text-[12px] font-medium">
      {(["en", "es"] as const).map((l) => (
        <button
          key={l}
          onClick={() => setLang(l)}
          className={`rounded-[6px] px-2 py-1 uppercase tracking-wide transition-colors ${
            lang === l ? "bg-surface-2 text-text-1" : "text-text-3 hover:text-text-2"
          }`}
          aria-pressed={lang === l}
        >
          {l}
        </button>
      ))}
    </div>
  );
}
