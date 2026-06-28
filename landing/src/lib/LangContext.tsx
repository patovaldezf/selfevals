"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { safeGetItem, safeSetItem } from "./browserStorage";
import { dict, type Lang, type Dict } from "./i18n";

const LangCtx = createContext<{
  lang: Lang;
  setLang: (l: Lang) => void;
  t: Dict;
}>({ lang: "en", setLang: () => {}, t: dict.en });

export function LangProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>("en");

  useEffect(() => {
    const saved = safeGetItem("selfevals-lang") as Lang | null;
    if (saved === "en" || saved === "es") {
      setLangState(saved);
    } else if (navigator.language.toLowerCase().startsWith("es")) {
      setLangState("es");
    }
  }, []);

  const setLang = (l: Lang) => {
    setLangState(l);
    safeSetItem("selfevals-lang", l);
    document.documentElement.lang = l;
  };

  return (
    <LangCtx.Provider value={{ lang, setLang, t: dict[lang] }}>
      {children}
    </LangCtx.Provider>
  );
}

export const useLang = () => useContext(LangCtx);
