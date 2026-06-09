import type { Metadata, Viewport } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { LangProvider } from "@/lib/LangContext";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://selfevals.com"),
  title: "selfevals — Self-improving evals for AI agents",
  description:
    "CLI-first, self-improving evals framework. Point it at your agent, sweep the parameters you expose, and get a report that tells you which configuration to keep — with evidence, not intuition.",
  keywords: [
    "evals",
    "LLM evals",
    "AI agent evaluation",
    "agent testing",
    "CI evals",
    "selfevals",
  ],
  openGraph: {
    title: "selfevals — Self-improving evals for AI agents",
    description:
      "Stop guessing whether your agent got better. A CLI-first evals framework that earns the configuration you ship.",
    url: "https://selfevals.com",
    siteName: "selfevals",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "selfevals — Self-improving evals for AI agents",
    description:
      "A CLI-first evals framework that earns the configuration you ship.",
  },
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  themeColor: "#08090a",
  colorScheme: "dark",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>
        <LangProvider>{children}</LangProvider>
      </body>
    </html>
  );
}
