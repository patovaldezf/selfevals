<!--
  MessageThread: render a chat-messages payload as a readable thread.

  LangSmith-parity: an `llm_call` span's `messages` payload (and the request
  input in general) is a JSON list of `{role, content}` turns. Dumped as raw
  JSON it's unreadable; here each turn gets a role chip and its content
  rendered with whitespace preserved (so multi-line prompts / code read as
  written). Content may itself be a list of blocks (Anthropic-style
  `{type:'text'|'tool_use'|'tool_result', ...}`); we flatten those to text.

  Dependency-free by design — no markdown lib is vendored. We preserve line
  breaks and mono-render fenced blocks; that covers the common prompt shapes
  without pulling a parser into the bundle.
-->
<script lang="ts">
  type Block = { type?: string; text?: string; content?: unknown; [k: string]: unknown };
  type Turn = { role?: string; content?: unknown; [k: string]: unknown };

  let { text }: { text: string } = $props();

  /** A messages payload is a JSON array whose entries have a `role`. */
  function parseTurns(raw: string): Turn[] | null {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return null;
    }
    // Accept both a bare list and the `{messages: [...]}` envelope
    // (ConversationInput on the request side).
    const list = Array.isArray(parsed)
      ? parsed
      : parsed && typeof parsed === 'object' && Array.isArray((parsed as { messages?: unknown }).messages)
        ? (parsed as { messages: unknown[] }).messages
        : null;
    if (!list || list.length === 0) return null;
    const turns = list.filter(
      (t): t is Turn => !!t && typeof t === 'object' && 'role' in t
    );
    return turns.length > 0 ? turns : null;
  }

  /** Flatten a turn's content (string or list of typed blocks) to display text. */
  function contentToText(content: unknown): string {
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      return content
        .map((b) => blockToText(b as Block))
        .filter((s) => s.length > 0)
        .join('\n');
    }
    if (content && typeof content === 'object') return JSON.stringify(content, null, 2);
    return content == null ? '' : String(content);
  }

  function blockToText(b: Block): string {
    if (typeof b === 'string') return b;
    if (b.type === 'text' && typeof b.text === 'string') return b.text;
    if (b.type === 'tool_use') return `→ tool_use: ${b.name ?? ''}\n${JSON.stringify(b.input ?? {}, null, 2)}`;
    if (b.type === 'tool_result')
      return `← tool_result\n${typeof b.content === 'string' ? b.content : JSON.stringify(b.content ?? '', null, 2)}`;
    if (typeof b.text === 'string') return b.text;
    return JSON.stringify(b, null, 2);
  }

  const turns = $derived(parseTurns(text));

  function roleClass(role: string | undefined): string {
    const r = (role ?? '').toLowerCase();
    if (r === 'user' || r === 'human') return 'role-user';
    if (r === 'assistant' || r === 'model' || r === 'ai') return 'role-assistant';
    if (r === 'system') return 'role-system';
    if (r === 'tool') return 'role-tool';
    return 'role-other';
  }
</script>

{#if turns}
  <div class="thread">
    {#each turns as turn, i (i)}
      <div class="turn">
        <span class="role {roleClass(turn.role)}">{turn.role ?? 'message'}</span>
        <pre class="content">{contentToText(turn.content)}</pre>
      </div>
    {/each}
  </div>
{:else}
  <!-- Not a recognizable message list — show the raw text as-is. -->
  <pre class="content raw">{text}</pre>
{/if}

<style>
  .thread {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .turn {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .role {
    align-self: flex-start;
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0.1rem 0.45rem;
    border-radius: var(--radius-sm);
    border: 1px solid var(--color-border);
    color: var(--color-text-2);
    background: var(--color-surface-2);
  }
  .role-user {
    color: var(--color-brand-strong);
    border-color: color-mix(in oklab, var(--color-brand) 40%, var(--color-border));
  }
  .role-assistant {
    color: var(--color-ok);
    border-color: color-mix(in oklab, var(--color-ok) 40%, var(--color-border));
  }
  .role-system {
    color: var(--color-text-3);
  }
  .role-tool {
    color: var(--color-warn);
    border-color: color-mix(in oklab, var(--color-warn) 40%, var(--color-border));
  }
  .content {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    line-height: 1.55;
    background: var(--color-surface-2);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: 0.65rem 0.75rem;
    margin: 0;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 24rem;
    color: var(--color-text-1);
  }
  .content.raw {
    max-height: 28rem;
  }
</style>
