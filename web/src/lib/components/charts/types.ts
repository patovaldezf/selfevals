/** Shared shapes for the data-viz kit. Kept in a plain .ts module so Svelte
 *  components can import them as types without the `export type` inside a
 *  `<script>` (which Svelte's parser rejects). */
export type Point = { x: number | string; y: number };
export type Series = { name?: string; points: Point[]; color?: string };
export type Bar = { label: string; value: number; sublabel?: string; color?: string };
