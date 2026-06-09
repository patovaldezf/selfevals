"use client";

import { useLang } from "@/lib/LangContext";
import { APP_URL } from "@/lib/i18n";
import { Logo, GitHubIcon } from "./primitives";

const GITHUB = "https://github.com/patovaldezf/selfevals";

export default function Footer() {
  const { t } = useLang();

  const cols = [
    {
      head: t.footer.product,
      links: [
        { label: t.footer.links.features, href: "#features" },
        { label: t.footer.links.quickstart, href: "#how" },
        { label: t.footer.links.webApp, href: APP_URL },
      ],
    },
    {
      head: t.footer.resources,
      links: [
        { label: t.footer.links.docs, href: `${GITHUB}#readme` },
        { label: t.footer.links.cli, href: `${GITHUB}#cli-reference` },
        { label: t.footer.links.caseStudy, href: "#case" },
      ],
    },
    {
      head: t.footer.company,
      links: [
        { label: t.footer.links.github, href: GITHUB },
        { label: t.footer.links.license, href: `${GITHUB}/blob/main/LICENSE` },
      ],
    },
  ];

  return (
    <footer className="border-t border-line bg-bg-2/40">
      <div className="mx-auto max-w-[1180px] px-5 py-16 sm:px-8">
        <div className="grid gap-10 sm:grid-cols-2 lg:grid-cols-[1.4fr_1fr_1fr_1fr]">
          <div>
            <Logo />
            <p className="mt-4 max-w-[260px] text-[14px] leading-relaxed text-text-2">
              {t.footer.tagline}
            </p>
            <a
              href={GITHUB}
              target="_blank"
              rel="noreferrer"
              className="mt-5 inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-[13px] text-text-2 transition-colors hover:text-text-1"
            >
              <GitHubIcon />
              GitHub
            </a>
          </div>

          {cols.map((col) => (
            <div key={col.head}>
              <h3 className="text-[12px] font-semibold uppercase tracking-wide text-text-3">
                {col.head}
              </h3>
              <ul className="mt-4 space-y-2.5">
                {col.links.map((l) => (
                  <li key={l.label}>
                    <a
                      href={l.href}
                      target={l.href.startsWith("#") ? undefined : "_blank"}
                      rel={l.href.startsWith("#") ? undefined : "noreferrer"}
                      className="text-[14px] text-text-2 transition-colors hover:text-text-1"
                    >
                      {l.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-14 flex flex-col items-center justify-between gap-3 border-t border-line pt-7 sm:flex-row">
          <p className="font-mono text-[12px] text-text-3">
            © <span className="tnum">2026</span> selfevals · {t.footer.rights}
          </p>
          <p className="font-mono text-[12px] text-text-3">selfevals.com</p>
        </div>
      </div>
    </footer>
  );
}
