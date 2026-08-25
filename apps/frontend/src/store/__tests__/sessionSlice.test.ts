import { describe, expect, it } from 'vitest';
import reducer, {
  answerStarted,
  citationsReceived,
  contextItemDropped,
  deltaReceived,
  hydrated,
  questionAsked,
  traceReceived,
  turnFailed,
  turnFinished,
} from '../sessionSlice';
import type { SessionState } from '../sessionSlice';
import type { BudgetOut } from '@/api/generated/model';

const budget = (): BudgetOut => ({
  max_tokens: 1000,
  available_tokens: 800,
  used_tokens: 500,
  utilisation: 0.625,
  over_budget: false,
  segments: [
    { context_class: 'system', token_count: 100, item_count: 1, fraction: 0.125 },
    { context_class: 'pinned', token_count: 0, item_count: 0, fraction: 0 },
    { context_class: 'history', token_count: 100, item_count: 1, fraction: 0.125 },
    { context_class: 'retrieved', token_count: 300, item_count: 2, fraction: 0.375 },
  ],
  items: [
    { item_id: 'sys', context_class: 'system', token_count: 100, label: 'System', relevance: null, evicts_next: false },
    { item_id: 'h1', context_class: 'history', token_count: 100, label: 'Turn 1', relevance: null, evicts_next: false },
    { item_id: 'c1', context_class: 'retrieved', token_count: 200, label: 'Doc A', relevance: 0.9, evicts_next: true },
    { item_id: 'c2', context_class: 'retrieved', token_count: 100, label: 'Doc B', relevance: 0.4, evicts_next: false },
  ],
});

const withBudget = (): SessionState =>
  reducer(undefined, hydrated({ budget: budget() }));

describe('turn lifecycle', () => {
  it('records a question and marks the session awaiting', () => {
    const state = reducer(undefined, questionAsked({ id: 'q1', text: 'hello?' }));
    expect(state.turns).toHaveLength(1);
    expect(state.turns[0]).toMatchObject({ role: 'user', text: 'hello?' });
    expect(state.awaiting).toBe(true);
  });

  it('appends streamed deltas to the assistant turn', () => {
    let state = reducer(undefined, questionAsked({ id: 'q1', text: 'hi' }));
    state = reducer(state, answerStarted({ id: 'a1' }));
    state = reducer(state, deltaReceived('Rev'));
    state = reducer(state, deltaReceived('enue rose.'));
    expect(state.turns[1]?.text).toBe('Revenue rose.');
    expect(state.turns[1]?.streaming).toBe(true);
  });

  it('never writes a delta onto a user turn', () => {
    const state = reducer(
      reducer(undefined, questionAsked({ id: 'q1', text: 'hi' })),
      deltaReceived('stray'),
    );
    expect(state.turns[0]?.text).toBe('hi');
  });

  it('collects trace events in order', () => {
    let state = reducer(undefined, answerStarted({ id: 'a1' }));
    state = reducer(state, traceReceived({ stage: 'dense_recall' } as never));
    state = reducer(state, traceReceived({ stage: 'fusion' } as never));
    expect(state.turns[0]?.trace.map((t) => t.stage)).toEqual([
      'dense_recall',
      'fusion',
    ]);
  });

  it('replaces citations wholesale rather than appending', () => {
    let state = reducer(undefined, answerStarted({ id: 'a1' }));
    state = reducer(state, citationsReceived([{ chunk_id: 'a' } as never]));
    state = reducer(state, citationsReceived([{ chunk_id: 'b' } as never]));
    expect(state.turns[0]?.citations).toHaveLength(1);
  });

  it('clears awaiting and stores the budget when the turn finishes', () => {
    let state = reducer(undefined, questionAsked({ id: 'q1', text: 'hi' }));
    state = reducer(state, answerStarted({ id: 'a1' }));
    state = reducer(
      state,
      turnFinished({ budget: budget(), degraded: ['no reranker'], branched: true }),
    );
    expect(state.awaiting).toBe(false);
    expect(state.turns[1]?.streaming).toBe(false);
    expect(state.degraded).toEqual(['no reranker']);
    expect(state.branched).toBe(true);
  });

  it('records a failure on the turn and stops awaiting', () => {
    let state = reducer(undefined, answerStarted({ id: 'a1' }));
    state = reducer(state, turnFailed('stream died'));
    expect(state.turns[0]?.error).toBe('stream died');
    expect(state.turns[0]?.streaming).toBe(false);
    expect(state.awaiting).toBe(false);
  });

  it('resets degradation flags when a new question is asked', () => {
    let state = reducer(undefined, answerStarted({ id: 'a1' }));
    state = reducer(state, turnFinished({ budget: budget(), degraded: ['x'], branched: true }));
    state = reducer(state, questionAsked({ id: 'q2', text: 'next' }));
    expect(state.degraded).toEqual([]);
    expect(state.branched).toBe(false);
  });
});

describe('context truncation (ADR-0008)', () => {
  it('drops an item and frees its tokens', () => {
    const state = reducer(withBudget(), contextItemDropped('c1'));
    expect(state.budget?.items.map((i) => i.item_id)).not.toContain('c1');
    expect(state.budget?.used_tokens).toBe(300);
  });

  it('recomputes utilisation after a drop', () => {
    const state = reducer(withBudget(), contextItemDropped('c1'));
    expect(state.budget?.utilisation).toBeCloseTo(300 / 800);
  });

  it('updates the segment the item came from', () => {
    const state = reducer(withBudget(), contextItemDropped('c1'));
    const retrieved = state.budget?.segments.find(
      (s) => s.context_class === 'retrieved',
    );
    expect(retrieved?.token_count).toBe(100);
    expect(retrieved?.item_count).toBe(1);
  });

  it('refuses to drop system context', () => {
    // The server refuses this too; the client must not appear to succeed.
    const state = reducer(withBudget(), contextItemDropped('sys'));
    expect(state.budget?.items.map((i) => i.item_id)).toContain('sys');
    expect(state.budget?.used_tokens).toBe(500);
  });

  it('ignores an unknown item id', () => {
    const state = reducer(withBudget(), contextItemDropped('nope'));
    expect(state.budget?.used_tokens).toBe(500);
  });

  it('records the drop as pending server confirmation', () => {
    // The client proposes; the server disposes (ADR-0007).
    const state = reducer(withBudget(), contextItemDropped('c1'));
    expect(state.pendingDrops).toEqual(['c1']);
  });

  it('clears pending drops once the server sends a fresh budget', () => {
    let state = reducer(withBudget(), contextItemDropped('c1'));
    state = reducer(
      state,
      turnFinished({ budget: budget(), degraded: [], branched: false }),
    );
    expect(state.pendingDrops).toEqual([]);
    // The server's budget wins: the item is back because the server kept it.
    expect(state.budget?.items.map((i) => i.item_id)).toContain('c1');
  });

  it('does nothing when there is no budget yet', () => {
    const state = reducer(undefined, contextItemDropped('c1'));
    expect(state.budget).toBeNull();
  });
});

describe('hydration (ADR-0007)', () => {
  it('replaces the projection from the server', () => {
    const state = reducer(undefined, hydrated({ sessionId: 's1', modelId: 'm1' }));
    expect(state.sessionId).toBe('s1');
    expect(state.modelId).toBe('m1');
  });

  it('settles anything the client was still proposing', () => {
    let state = reducer(withBudget(), contextItemDropped('c1'));
    expect(state.pendingDrops).toHaveLength(1);
    state = reducer(state, hydrated({ budget: budget() }));
    expect(state.pendingDrops).toEqual([]);
  });
});
