"use client";

import { useEffect, useRef, useState } from "react";

/* A line in the streamed eval run. `kind` drives color. */
type Line = {
  text: string;
  kind?: "cmd" | "dim" | "pass" | "fail" | "head" | "accent" | "warn";
  /** ms to wait before this line appears (relative to previous) */
  after?: number;
};

const SCRIPT: Line[] = [
  { text: "$ selfevals run evals/experiments/sentiment.yaml", kind: "cmd", after: 0 },
  { text: "", after: 260 },
  { text: "loaded experiment · 3 cases · grid sweep temperature ∈ {0.0, 0.5, 1.0}", kind: "dim", after: 360 },
  { text: "", after: 120 },
  { text: "iteration 1/3   temperature=0.0", kind: "head", after: 420 },
  { text: "  ✓ sentiment_classification    pass@1 1.00", kind: "pass", after: 180 },
  { text: "  ✓ structured_extraction       pass@1 1.00", kind: "pass", after: 170 },
  { text: "  ✓ open_ended_reply  (judge)   pass@1 0.67", kind: "pass", after: 190 },
  { text: "", after: 100 },
  { text: "iteration 2/3   temperature=0.5", kind: "head", after: 360 },
  { text: "  ✓ sentiment_classification    pass@1 1.00", kind: "pass", after: 170 },
  { text: "  ✓ structured_extraction       pass@1 0.67", kind: "warn", after: 170 },
  { text: "  ✓ open_ended_reply  (judge)   pass@1 1.00", kind: "pass", after: 180 },
  { text: "", after: 100 },
  { text: "iteration 3/3   temperature=1.0", kind: "head", after: 360 },
  { text: "  ✓ sentiment_classification    pass@1 1.00", kind: "pass", after: 170 },
  { text: "  ✗ structured_extraction       pass@1 0.33", kind: "fail", after: 170 },
  { text: "  ✓ open_ended_reply  (judge)   pass@1 1.00", kind: "pass", after: 180 },
  { text: "", after: 220 },
  { text: "decision matrix → keep iteration 1  (temperature=0.0)", kind: "accent", after: 420 },
  { text: "  best aggregate pass@1 0.89 · regression gate ✓ passed", kind: "dim", after: 160 },
  { text: "  report → ./reports/sentiment.md", kind: "dim", after: 160 },
  { text: "", after: 200 },
  { text: "✓ done in 0.84s", kind: "accent", after: 220 },
];

const COLORS: Record<NonNullable<Line["kind"]>, string> = {
  cmd: "text-text-1",
  dim: "text-text-3",
  pass: "text-accent",
  fail: "text-[#f76d6d]",
  warn: "text-[#f5b14b]",
  head: "text-text-2",
  accent: "text-accent",
};

export default function Terminal() {
  const [count, setCount] = useState(0);
  const [done, setDone] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting && !started.current) {
          started.current = true;
          play();
          io.disconnect();
        }
      },
      { threshold: 0.35 },
    );
    io.observe(el);
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function play() {
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (reduce) {
      setCount(SCRIPT.length);
      setDone(true);
      return;
    }

    let i = 0;
    const tick = () => {
      if (i >= SCRIPT.length) {
        setDone(true);
        // loop after a beat
        setTimeout(() => {
          setDone(false);
          setCount(0);
          i = 0;
          setTimeout(tick, 900);
        }, 4200);
        return;
      }
      i += 1;
      setCount(i);
      const next = SCRIPT[i]?.after ?? 160;
      setTimeout(tick, next);
    };
    setTimeout(tick, 400);
  }

  return (
    <div ref={wrapRef} className="grad-border relative rounded-xl bg-[#0a0c0d] shadow-[0_24px_70px_-20px_rgba(0,0,0,0.8)]">
      {/* chrome */}
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <span className="flex gap-1.5">
          <span className="h-3 w-3 rounded-full bg-[#ff5f56]" />
          <span className="h-3 w-3 rounded-full bg-[#ffbd2e]" />
          <span className="h-3 w-3 rounded-full bg-[#27c93f]" />
        </span>
        <span className="ml-2 font-mono text-[12px] text-text-3">
          selfevals — run
        </span>
        <span className="ml-auto font-mono text-[11px] text-text-3">zsh</span>
      </div>

      {/* body */}
      <div className="h-[372px] overflow-hidden px-5 py-4 font-mono text-[13px] leading-[1.7] sm:text-[13.5px]">
        {SCRIPT.slice(0, count).map((line, idx) => (
          <div
            key={idx}
            className={`${line.kind ? COLORS[line.kind] : "text-text-2"} whitespace-pre`}
          >
            {line.text || " "}
            {idx === count - 1 && !done && line.text ? (
              <span className="caret align-middle" />
            ) : null}
          </div>
        ))}
        {done && (
          <div className="text-text-3 whitespace-pre">
            $ <span className="caret align-middle" />
          </div>
        )}
      </div>
    </div>
  );
}
