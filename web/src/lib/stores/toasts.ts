/**
 * Tiny toast store — no dependency, Sonner-shaped API. A toast is a transient
 * message with a kind (success/error/info) and an auto-dismiss timer; error
 * toasts stick around longer because they carry something the user must read.
 *
 * Usage: `toast.success('Dataset frozen')`, `toast.error(err.detail)`.
 * Mount `<Toaster />` once in the root layout to render them.
 */
import { writable } from 'svelte/store';

export type ToastKind = 'success' | 'error' | 'info';

export type Toast = {
  id: number;
  kind: ToastKind;
  message: string;
  /** Optional second line for detail (e.g. an error body). */
  description?: string;
};

const DEFAULT_TTL: Record<ToastKind, number> = {
  success: 3000,
  info: 4000,
  error: 6000
};

function createToasts() {
  const { subscribe, update } = writable<Toast[]>([]);
  let seq = 0;

  function push(kind: ToastKind, message: string, description?: string): number {
    const id = ++seq;
    update((list) => [...list, { id, kind, message, description }]);
    const ttl = DEFAULT_TTL[kind];
    if (ttl > 0 && typeof window !== 'undefined') {
      window.setTimeout(() => dismiss(id), ttl);
    }
    return id;
  }

  function dismiss(id: number) {
    update((list) => list.filter((t) => t.id !== id));
  }

  return {
    subscribe,
    dismiss,
    success: (message: string, description?: string) => push('success', message, description),
    error: (message: string, description?: string) => push('error', message, description),
    info: (message: string, description?: string) => push('info', message, description)
  };
}

export const toast = createToasts();
