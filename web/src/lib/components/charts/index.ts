/**
 * Data-viz kit barrel. Import charts from one place:
 *   import { LineChart, BarChart, Funnel, StatRing, DeltaStat } from '$lib/components/charts';
 *
 * Every chart speaks the shared threshold language (viz/thresholds.ts) — pass a
 * `{ target, direction }` and green/amber/red follows automatically.
 */
export { default as LineChart } from './LineChart.svelte';
export { default as AreaChart } from './AreaChart.svelte';
export { default as BarChart } from './BarChart.svelte';
export { default as Sparkline } from './Sparkline.svelte';
export { default as Funnel } from './Funnel.svelte';
export { default as StatRing } from './StatRing.svelte';
export { default as Donut } from './Donut.svelte';
export { default as Heatmap } from './Heatmap.svelte';
export { default as DeltaStat } from './DeltaStat.svelte';
export { default as CountUp } from './CountUp.svelte';

export { formatValue, formatDelta, svgId, type ValueFormat } from './format';
export type { Point, Series, Bar } from './types';
