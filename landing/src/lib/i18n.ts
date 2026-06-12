export type Lang = "en" | "es";

export const APP_URL =
  process.env.NEXT_PUBLIC_APP_URL ?? "https://app.selfevals.com";

const dictDef = {
  en: {
    nav: {
      product: "Product",
      howItWorks: "How it works",
      caseStudy: "Case study",
      docs: "Docs",
      login: "Log in",
      signup: "Sign up",
    },
    hero: {
      badge: "v0.5.0 — runtime functional",
      title1: "Stop guessing whether",
      title2: "your agent got better.",
      sub: "selfevals is a CLI-first, self-improving evals framework. Point it at your agent, sweep the parameters you expose, and get a report that tells you which configuration to keep — with evidence, not intuition.",
      ctaPrimary: "Run your first eval",
      ctaSecondary: "Read the docs",
      install: "pip install selfevals",
      copied: "Copied",
    },
    trust: {
      label: "Agnostic to the agent framework underneath",
    },
    features: {
      eyebrow: "Why selfevals",
      title: "An evals harness that earns the configuration you ship.",
      sub: "Five nouns, one YAML spec, a closed feedback loop. selfevals never calls your provider — your agent does, and selfevals grades the result.",
      items: [
        {
          tag: "Adapters",
          title: "Point it at any agent",
          body: "Embedded callable, CLI subprocess, or HTTP endpoint. selfevals calls your agent, never the provider directly — so it stays framework-agnostic from day one.",
        },
        {
          tag: "Graders",
          title: "Deterministic and LLM-judge",
          body: "Score traces with rules — substrings, tools, JSON schema — or a rubric-driven judge. Per-grader scoring reports each grader's own pass@1 instead of a blunt worst-of.",
        },
        {
          tag: "Proposers",
          title: "Sweep the parameter space",
          body: "Grid, random, or manual. The grid proposer enumerates its full cartesian product instead of early-stopping on a plateau — no combination left untried.",
        },
        {
          tag: "Decision matrix",
          title: "A verdict, not a number",
          body: "Each iteration's metrics become a decision: keep, reject, investigate, spawn a sub-experiment, or require a tradeoff review.",
        },
        {
          tag: "Error analysis",
          title: "A taxonomy that grows itself",
          body: "selfevals maintains a per-workspace failure-mode taxonomy and drives the next experiment from it — a closed loop, with a human in the promote step.",
        },
        {
          tag: "Reports",
          title: "Markdown or JSON, ranked",
          body: "Iterations ranked, the winner selected, a top failure-modes table — end-to-end in under a second against the bundled echo agent. No API key needed.",
        },
      ],
    },
    how: {
      eyebrow: "60-second quickstart",
      title: "From install to a ranked report in one command.",
      sub: "No dashboard to configure, no provider to wire up first. The CLI orchestrates the whole run.",
      steps: [
        { n: "01", t: "Copy an example", d: "Seed evals/ into your project with one command." },
        { n: "02", t: "Run the experiment", d: "Cases flow through your adapter, traces get graded, iterations persist." },
        { n: "03", t: "Read the verdict", d: "A ranked markdown report names the configuration to keep." },
      ],
    },
    terminal: {
      eyebrow: "The money shot",
      title: "Watch an experiment converge.",
      sub: "Three eval cases, a temperature sweep, a winner selected — live in your terminal.",
    },
    caseStudy: {
      eyebrow: "Case study · brain_os",
      title: "A framework improved by the agent it was grading.",
      body: "brain_os is a memory OS for AI agents. It points selfevals at its own hybrid retriever and runs a parameter sweep over its retrieval config. On its golden set it measures MRR 0.896 / Recall@8 1.0 — with a CI regression gate at MRR ≥ 0.80.",
      body2: "Running the sweep surfaced two limitations in selfevals itself: a grid proposer that early-stopped on a plateau, and a conjunctive pass@1 that masked each grader's signal. Both became the headline features of v0.5.0. The experiment did its job — it relocated brain_os's bottleneck with evidence, not intuition.",
      stats: [
        { v: "0.896", l: "MRR on golden set" },
        { v: "1.0", l: "Recall@8" },
        { v: "5", l: "deterministic graders" },
        { v: "≥0.80", l: "CI regression gate" },
      ],
    },
    compare: {
      eyebrow: "Why CLI-first",
      title: "Built for the loop you already work in.",
      sub: "Not a dashboard you log into — a tool that runs where your code runs.",
      rows: [
        { f: "Runs in CI without a hosted service", se: true, other: false },
        { f: "Never calls your provider — your agent does", se: true, other: false },
        { f: "Framework-agnostic adapters", se: true, other: "partial" },
        { f: "Closed error-analysis loop with a taxonomy", se: true, other: false },
        { f: "Per-grader scoring, not a blunt worst-of", se: true, other: "partial" },
        { f: "Multi-tenant from day one", se: true, other: true },
      ],
      seHead: "selfevals",
      otherHead: "Hosted eval dashboards",
    },
    proof: {
      eyebrow: "From the field",
      quote:
        "It relocated our retrieval bottleneck to upstream task-shape classification — with evidence, not intuition. Then it improved itself off the back of our run.",
      author: "brain_os",
      role: "memory OS · production integration",
    },
    cta: {
      title: "Grade your agent like you mean it.",
      sub: "Install the CLI, run the bundled example offline, and see a ranked report in under a second.",
      primary: "Get started",
      secondary: "Star on GitHub",
      install: "pip install selfevals",
    },
    footer: {
      tagline: "Self-improving evals framework for AI agents.",
      product: "Product",
      resources: "Resources",
      company: "Company",
      links: {
        features: "Features",
        quickstart: "Quickstart",
        webApp: "Web app",
        docs: "Docs",
        cli: "CLI reference",
        github: "GitHub",
        caseStudy: "Case study",
        license: "License",
      },
      rights: "Apache-2.0 licensed.",
    },
  },
  es: {
    nav: {
      product: "Producto",
      howItWorks: "Cómo funciona",
      caseStudy: "Caso de uso",
      docs: "Docs",
      login: "Entrar",
      signup: "Crear cuenta",
    },
    hero: {
      badge: "v0.5.0 — runtime funcional",
      title1: "Deja de adivinar si",
      title2: "tu agente mejoró.",
      sub: "selfevals es un framework de evals CLI-first que se auto-mejora. Apúntalo a tu agente, barre los parámetros que expones y obtén un reporte que te dice qué configuración conservar — con evidencia, no intuición.",
      ctaPrimary: "Corre tu primer eval",
      ctaSecondary: "Leer los docs",
      install: "pip install selfevals",
      copied: "Copiado",
    },
    trust: {
      label: "Agnóstico al framework de agentes que tengas debajo",
    },
    features: {
      eyebrow: "Por qué selfevals",
      title: "Un harness de evals que se gana la configuración que envías.",
      sub: "Cinco sustantivos, un spec YAML, un loop de feedback cerrado. selfevals nunca llama a tu proveedor — lo hace tu agente, y selfevals califica el resultado.",
      items: [
        {
          tag: "Adapters",
          title: "Apúntalo a cualquier agente",
          body: "Callable embebido, subproceso CLI o endpoint HTTP. selfevals llama a tu agente, nunca al proveedor directo — agnóstico al framework desde el día uno.",
        },
        {
          tag: "Graders",
          title: "Determinista y juez-LLM",
          body: "Califica trazas con reglas — substrings, tools, JSON schema — o un juez guiado por rúbrica. El scoring por grader reporta el pass@1 de cada uno en vez de un worst-of romo.",
        },
        {
          tag: "Proposers",
          title: "Barre el espacio de parámetros",
          body: "Grid, random o manual. El proposer de grid enumera su producto cartesiano completo en vez de frenar en una meseta — ninguna combinación queda sin probar.",
        },
        {
          tag: "Decision matrix",
          title: "Un veredicto, no un número",
          body: "Las métricas de cada iteración se vuelven una decisión: conservar, rechazar, investigar, lanzar un sub-experimento o pedir revisión de tradeoffs.",
        },
        {
          tag: "Error analysis",
          title: "Una taxonomía que crece sola",
          body: "selfevals mantiene una taxonomía de failure modes por workspace y dirige el siguiente experimento desde ella — un loop cerrado, con un humano en el paso de promoción.",
        },
        {
          tag: "Reportes",
          title: "Markdown o JSON, rankeado",
          body: "Iteraciones rankeadas, el ganador seleccionado, una tabla de failure modes — end-to-end en menos de un segundo contra el agente echo incluido. Sin API key.",
        },
      ],
    },
    how: {
      eyebrow: "Quickstart de 60 segundos",
      title: "De instalar a un reporte rankeado en un comando.",
      sub: "Sin dashboard que configurar, sin proveedor que cablear primero. El CLI orquesta todo el run.",
      steps: [
        { n: "01", t: "Copia un ejemplo", d: "Siembra evals/ en tu proyecto con un comando." },
        { n: "02", t: "Corre el experimento", d: "Los casos fluyen por tu adapter, las trazas se califican, las iteraciones persisten." },
        { n: "03", t: "Lee el veredicto", d: "Un reporte markdown rankeado nombra la configuración a conservar." },
      ],
    },
    terminal: {
      eyebrow: "La toma estrella",
      title: "Mira un experimento converger.",
      sub: "Tres eval cases, un barrido de temperatura, un ganador seleccionado — en vivo en tu terminal.",
    },
    caseStudy: {
      eyebrow: "Caso de uso · brain_os",
      title: "Un framework mejorado por el agente que estaba calificando.",
      body: "brain_os es un OS de memoria para agentes de IA. Apunta selfevals a su propio retriever híbrido y corre un barrido de parámetros sobre su config de retrieval. En su golden set mide MRR 0.896 / Recall@8 1.0 — con un gate de regresión en CI de MRR ≥ 0.80.",
      body2: "Correr el barrido expuso dos límites del propio selfevals: un proposer de grid que frenaba en una meseta, y un pass@1 conjuntivo que enmascaraba la señal de cada grader. Ambos se volvieron las features estrella de v0.5.0. El experimento hizo su trabajo — reubicó el cuello de botella de brain_os con evidencia, no intuición.",
      stats: [
        { v: "0.896", l: "MRR en golden set" },
        { v: "1.0", l: "Recall@8" },
        { v: "5", l: "graders deterministas" },
        { v: "≥0.80", l: "gate de regresión CI" },
      ],
    },
    compare: {
      eyebrow: "Por qué CLI-first",
      title: "Hecho para el loop en el que ya trabajas.",
      sub: "No un dashboard al que entras — una herramienta que corre donde corre tu código.",
      rows: [
        { f: "Corre en CI sin servicio hosteado", se: true, other: false },
        { f: "Nunca llama a tu proveedor — lo hace tu agente", se: true, other: false },
        { f: "Adapters agnósticos al framework", se: true, other: "partial" },
        { f: "Loop cerrado de error-analysis con taxonomía", se: true, other: false },
        { f: "Scoring por grader, no un worst-of romo", se: true, other: "partial" },
        { f: "Multi-tenant desde el día uno", se: true, other: true },
      ],
      seHead: "selfevals",
      otherHead: "Dashboards de evals hosteados",
    },
    proof: {
      eyebrow: "Desde el campo",
      quote:
        "Reubicó nuestro cuello de botella de retrieval hacia la clasificación de task-shape upstream — con evidencia, no intuición. Y luego se mejoró a sí mismo a partir de nuestro run.",
      author: "brain_os",
      role: "memory OS · integración en producción",
    },
    cta: {
      title: "Califica tu agente en serio.",
      sub: "Instala el CLI, corre el ejemplo incluido offline, y mira un reporte rankeado en menos de un segundo.",
      primary: "Empezar",
      secondary: "Estrella en GitHub",
      install: "pip install selfevals",
    },
    footer: {
      tagline: "Framework de evals que se auto-mejora para agentes de IA.",
      product: "Producto",
      resources: "Recursos",
      company: "Compañía",
      links: {
        features: "Features",
        quickstart: "Quickstart",
        webApp: "App web",
        docs: "Docs",
        cli: "Referencia CLI",
        github: "GitHub",
        caseStudy: "Caso de uso",
        license: "Licencia",
      },
      rights: "Licencia Apache-2.0.",
    },
  },
} as const;

// Widen the literal string types so EN and ES share one structural type.
type Widen<T> = T extends string
  ? string
  : T extends readonly (infer U)[]
    ? Widen<U>[]
    : { -readonly [K in keyof T]: Widen<T[K]> };

export type Dict = Widen<(typeof dictDef)["en"]>;
export const dict = dictDef as unknown as Record<Lang, Dict>;
