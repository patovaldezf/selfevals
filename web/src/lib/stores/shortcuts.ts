/**
 * Global keyboard shortcuts — the keyboard-first layer over the app.
 *
 * Two kinds are handled here:
 *   - single chords like `?` (show help) and `⌘K` (palette), and
 *   - two-key sequences like `g e` (go to experiments), Gmail/Linear-style,
 *     where pressing `g` arms a leader and the next key completes it within a
 *     short window.
 *
 * Bindings are registered with handlers; the AppShell mounts a single window
 * listener that dispatches to them. Typing in an input/textarea/select or with
 * a modifier (other than the palette's ⌘K) is ignored so shortcuts never fight
 * with text entry. Per-page lists/rows handle their own j/k locally.
 */
import { writable, type Readable, get } from 'svelte/store';

export type Shortcut = {
  /** Sequence of keys. One entry = single chord ('?'); two = leader sequence
   *  (['g','e']). Keys are matched case-insensitively against event.key. */
  keys: string[];
  /** Human label for the help sheet. */
  label: string;
  /** Group header in the help sheet ("Navigate", "Actions"). */
  group?: string;
  run: () => void;
};

const LEADER_WINDOW_MS = 900;

function createShortcuts() {
  const store = writable<Map<string, Shortcut>>(new Map());
  const { subscribe, update } = store;

  function register(list: Shortcut[]): () => void {
    update((m) => {
      const next = new Map(m);
      for (const s of list) next.set(s.keys.join(' '), s);
      return next;
    });
    return () =>
      update((m) => {
        const next = new Map(m);
        for (const s of list) next.delete(s.keys.join(' '));
        return next;
      });
  }

  return { subscribe, register, _store: store as Readable<Map<string, Shortcut>> };
}

export const shortcuts = createShortcuts();

/** Whether the help sheet (`?`) is open. */
export const helpOpen = writable(false);

function isTypingTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
}

/**
 * Wire the global listener. Call once from the AppShell (returns a teardown).
 * `onPalette` is invoked for ⌘K / Ctrl+K regardless of focus, matching the
 * universal convention.
 */
export function mountShortcutListener(onPalette: () => void): () => void {
  let leader: string | null = null;
  let leaderTimer: ReturnType<typeof setTimeout> | null = null;

  function clearLeader() {
    leader = null;
    if (leaderTimer) {
      clearTimeout(leaderTimer);
      leaderTimer = null;
    }
  }

  function onKeydown(e: KeyboardEvent) {
    // ⌘K / Ctrl+K opens the palette from anywhere, even inside inputs.
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      onPalette();
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (isTypingTarget(e.target)) return;

    const map = get(shortcuts as unknown as Readable<Map<string, Shortcut>>);
    const key = e.key.toLowerCase();

    if (leader) {
      const combo = `${leader} ${key}`;
      const seq = map.get(combo);
      clearLeader();
      if (seq) {
        e.preventDefault();
        seq.run();
      }
      return;
    }

    // A registered single chord wins outright.
    const single = map.get(key);
    if (single) {
      e.preventDefault();
      single.run();
      return;
    }

    // Otherwise, if this key is the leader of any sequence, arm it.
    const armsSequence = [...map.keys()].some((k) => k.includes(' ') && k.startsWith(`${key} `));
    if (armsSequence) {
      e.preventDefault();
      leader = key;
      leaderTimer = setTimeout(clearLeader, LEADER_WINDOW_MS);
    }
  }

  window.addEventListener('keydown', onKeydown);
  return () => {
    window.removeEventListener('keydown', onKeydown);
    clearLeader();
  };
}
