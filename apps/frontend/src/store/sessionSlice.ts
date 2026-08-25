/**
 * Session context as a *projection* of server state (ADR-0007).
 *
 * The server is authoritative. This slice is the client's working copy: it
 * exists so the context meter can animate against local state at frame rate and
 * so a refresh is instant rather than blank. Nothing here is the source of
 * truth for anything sent to the model — the client proposes, the server
 * disposes, and the server's answer wins any disagreement.
 */

import { createSlice, type PayloadAction } from '@reduxjs/toolkit';
import type {
  BudgetOut,
  CitationOut,
  TraceEventOut,
} from '@/api/generated/model';

export interface Turn {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  citations: CitationOut[];
  trace: TraceEventOut[];
  streaming: boolean;
  error?: string;
}

export interface SessionState {
  sessionId: string | null;
  modelId: string | null;
  sourceIds: string[];
  turns: Turn[];
  budget: BudgetOut | null;
  degraded: string[];
  branched: boolean;
  /** True between submitting a question and the `finished` frame. */
  awaiting: boolean;
  /** Item ids the user has dropped locally, pending server confirmation. */
  pendingDrops: string[];
}

const initialState: SessionState = {
  sessionId: null,
  modelId: null,
  sourceIds: [],
  turns: [],
  budget: null,
  degraded: [],
  branched: false,
  awaiting: false,
  pendingDrops: [],
};

const sessionSlice = createSlice({
  name: 'session',
  initialState,
  reducers: {
    /** Replace the projection wholesale from the server (chat inception). */
    hydrated(state, action: PayloadAction<Partial<SessionState>>) {
      Object.assign(state, action.payload);
      // A hydrate is the server's answer, so anything the client was still
      // proposing is settled by definition.
      state.pendingDrops = [];
    },
    modelSelected(state, action: PayloadAction<string>) {
      state.modelId = action.payload;
    },
    sourcesSelected(state, action: PayloadAction<string[]>) {
      state.sourceIds = action.payload;
    },
    questionAsked(state, action: PayloadAction<{ id: string; text: string }>) {
      state.turns.push({
        id: action.payload.id,
        role: 'user',
        text: action.payload.text,
        citations: [],
        trace: [],
        streaming: false,
      });
      state.awaiting = true;
      state.degraded = [];
      state.branched = false;
    },
    answerStarted(state, action: PayloadAction<{ id: string }>) {
      state.turns.push({
        id: action.payload.id,
        role: 'assistant',
        text: '',
        citations: [],
        trace: [],
        streaming: true,
      });
    },
    traceReceived(state, action: PayloadAction<TraceEventOut>) {
      const turn = state.turns.at(-1);
      if (turn?.role === 'assistant') turn.trace.push(action.payload);
    },
    citationsReceived(state, action: PayloadAction<CitationOut[]>) {
      const turn = state.turns.at(-1);
      if (turn?.role === 'assistant') turn.citations = action.payload;
    },
    deltaReceived(state, action: PayloadAction<string>) {
      const turn = state.turns.at(-1);
      if (turn?.role === 'assistant') turn.text += action.payload;
    },
    turnFinished(
      state,
      action: PayloadAction<{
        budget: BudgetOut;
        degraded: string[];
        branched: boolean;
      }>,
    ) {
      const turn = state.turns.at(-1);
      if (turn?.role === 'assistant') turn.streaming = false;
      state.budget = action.payload.budget;
      state.degraded = action.payload.degraded;
      state.branched = action.payload.branched;
      state.awaiting = false;
      state.pendingDrops = [];
    },
    turnFailed(state, action: PayloadAction<string>) {
      const turn = state.turns.at(-1);
      if (turn?.role === 'assistant') {
        turn.streaming = false;
        turn.error = action.payload;
      }
      state.awaiting = false;
    },
    /**
     * Drop a context item optimistically (ADR-0008).
     *
     * Applied locally so the meter responds immediately, and recorded as
     * pending so the next server budget can overrule it. The client never
     * decides what the model sees.
     */
    contextItemDropped(state, action: PayloadAction<string>) {
      if (!state.budget) return;
      const item = state.budget.items.find((i) => i.item_id === action.payload);
      if (!item || item.context_class === 'system') return;

      state.pendingDrops.push(action.payload);
      state.budget.items = state.budget.items.filter(
        (i) => i.item_id !== action.payload,
      );
      state.budget.used_tokens -= item.token_count;
      state.budget.utilisation =
        state.budget.used_tokens / state.budget.available_tokens;
      state.budget.over_budget =
        state.budget.used_tokens > state.budget.available_tokens;

      const segment = state.budget.segments.find(
        (s) => s.context_class === item.context_class,
      );
      if (segment) {
        segment.token_count -= item.token_count;
        segment.item_count -= 1;
        segment.fraction = segment.token_count / state.budget.available_tokens;
      }
    },
    cleared() {
      return initialState;
    },
  },
});

export const {
  hydrated,
  modelSelected,
  sourcesSelected,
  questionAsked,
  answerStarted,
  traceReceived,
  citationsReceived,
  deltaReceived,
  turnFinished,
  turnFailed,
  contextItemDropped,
  cleared,
} = sessionSlice.actions;

export default sessionSlice.reducer;
