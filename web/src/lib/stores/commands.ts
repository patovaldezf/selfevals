/**
 * Command registry for the ⌘K palette. Pages register commands on mount and
 * unregister on destroy, so the palette always reflects what's reachable from
 * the current context (a trace page adds "Promote to dataset", the experiments
 * list adds "Run experiment", etc.). Navigation commands are registered once
 * globally from the AppShell.
 *
 * Keep this a plain registry — the palette UI owns fuzzy matching and keyboard
 * handling. A command's `run` is fired with no args; close-on-run is the
 * palette's job.
 */
import { writable, derived, type Readable } from 'svelte/store';
import type { ComponentType } from 'svelte';

export type Command = {
  /** Stable id so re-registration replaces rather than duplicates. */
  id: string;
  /** Primary label shown in the palette. */
  title: string;
  /** Optional grouping header ("Navigate", "Create", "This experiment"). */
  group?: string;
  /** Extra terms to match on beyond the title (ids, synonyms). */
  keywords?: string;
  /** lucide icon component. */
  icon?: ComponentType;
  /** Shortcut hint shown on the right (e.g. ['G', 'E']). Display only. */
  shortcut?: string[];
  /** What the command does. May navigate, open a modal, fire a mutation. */
  run: () => void;
};

function createRegistry() {
  const { subscribe, update } = writable<Map<string, Command>>(new Map());

  return {
    subscribe,
    /** Register (or replace) a set of commands. Returns an unregister fn so a
     *  page can `onDestroy(register([...]))`. */
    register(commands: Command[]): () => void {
      update((m) => {
        const next = new Map(m);
        for (const c of commands) next.set(c.id, c);
        return next;
      });
      return () => {
        update((m) => {
          const next = new Map(m);
          for (const c of commands) next.delete(c.id);
          return next;
        });
      };
    }
  };
}

export const commands = createRegistry();

/** Flat, stable-ordered list for the palette to filter. */
export const commandList: Readable<Command[]> = derived(commands, ($c) => [...$c.values()]);

/** Open/closed state of the palette, toggled by ⌘K and Escape. */
export const paletteOpen = writable(false);
