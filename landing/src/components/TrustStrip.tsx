"use client";

import { useLang } from "@/lib/LangContext";

const ADAPTERS = [
  "OpenAI",
  "Anthropic",
  "Bedrock",
  "Vertex",
  "LangChain",
  "CrewAI",
];

export default function TrustStrip() {
  const { t } = useLang();
  const items = [...ADAPTERS, ...ADAPTERS];

  return (
    <section className="border-y border-line bg-bg-2/60 py-9">
      <div className="mx-auto max-w-[1180px] px-5 sm:px-8">
        <p className="text-center font-mono text-[12px] uppercase tracking-[0.14em] text-text-3">
          {t.trust.label}
        </p>
        <div className="marquee-mask mt-6 overflow-hidden">
          <div className="marquee-track flex w-max items-center gap-12">
            {items.map((name, i) => (
              <span
                key={i}
                className="shrink-0 font-mono text-[15px] font-medium text-text-2/80"
              >
                {name}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
