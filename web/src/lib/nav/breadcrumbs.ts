/**
 * Breadcrumbs — derived from the URL so the global topbar shows the same trail
 * on every page without each route re-declaring its header.
 *
 * The path segments map 1:1 to crumbs. Route ids (an experiment id, a dataset
 * id) are opaque, so a detail page registers a human label for its id via the
 * `crumbLabels` store (e.g. `{ exp_123: "Retrieval sweep" }`); the topbar swaps
 * the id for the name when it has one and falls back to a prettified segment
 * otherwise. Everything here is pure + SSR-safe.
 */
import { writable } from 'svelte/store';

export type Crumb = { label: string; href: string };

/** Human labels for opaque path segments (ids), keyed by the raw segment.
 *  Detail pages set this on load; the topbar reads it. Cleared on navigate by
 *  the shell so stale names never linger across entities. */
export const crumbLabels = writable<Record<string, string>>({});

/** The workspace id is a crumb we render as a fixed home ("workspace"), never
 *  as its raw id — it's the root the sidebar already represents. */
const PRETTY: Record<string, string> = {
  experiments: 'Experiments',
  arenas: 'Arenas',
  datasets: 'Datasets',
  metrics: 'Metrics',
  'failure-modes': 'Failure modes',
  clusters: 'Clusters',
  'anchor-set': 'Anchor set',
  traces: 'Traces',
  threads: 'Threads',
  analyze: 'Analyze',
  new: 'New'
};

function pretty(segment: string): string {
  return PRETTY[segment] ?? segment.replace(/[-_]/g, ' ').replace(/^\w/, (c) => c.toUpperCase());
}

/**
 * Build the crumb trail for a pathname. The first segment is the workspace id,
 * rendered as "workspace" linking to the overview. Remaining segments become
 * crumbs, each linking to its own prefix so any level is clickable.
 */
export function crumbsFromPath(pathname: string, labels: Record<string, string> = {}): Crumb[] {
  const parts = pathname.split('/').filter(Boolean);
  if (parts.length === 0) return [];

  const [workspace, ...rest] = parts;
  const crumbs: Crumb[] = [{ label: 'Workspace', href: `/${workspace}` }];

  let acc = `/${workspace}`;
  for (const seg of rest) {
    acc += `/${seg}`;
    crumbs.push({ label: labels[seg] ?? pretty(seg), href: acc });
  }
  return crumbs;
}
