/**
 * Theme store: 'light' | 'dark', persisted to localStorage and applied to
 * <html data-theme>. Defaults to the OS preference on first visit. SSR-safe
 * (no-ops without `window`); the actual class is set client-side on init.
 */
import { writable } from 'svelte/store';

export type Theme = 'light' | 'dark';
const KEY = 'selfevals-theme';

function readInitial(): Theme {
  if (typeof window === 'undefined') return 'light';
  const stored = localStorage.getItem(KEY);
  if (stored === 'light' || stored === 'dark') return stored;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function apply(theme: Theme) {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset.theme = theme;
}

function createTheme() {
  const { subscribe, set, update } = writable<Theme>('light');

  return {
    subscribe,
    /** Call once on client mount to sync the store + <html> with storage/OS. */
    init() {
      const t = readInitial();
      apply(t);
      set(t);
    },
    toggle() {
      update((t) => {
        const next: Theme = t === 'dark' ? 'light' : 'dark';
        if (typeof window !== 'undefined') localStorage.setItem(KEY, next);
        apply(next);
        return next;
      });
    }
  };
}

export const theme = createTheme();
