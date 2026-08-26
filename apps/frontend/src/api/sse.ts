/**
 * SSE client for the chat stream.
 *
 * Hand-written rather than generated: the chat endpoint is a POST returning
 * `text/event-stream`, which OpenAPI cannot describe as anything richer than a
 * string, so orval has nothing useful to generate. The *payload* types are
 * still the generated ones — only the transport is bespoke.
 *
 * `EventSource` is not usable here either: it only issues GET requests and
 * cannot send a JSON body.
 */

import type {
  BudgetOut,
  CitationOut,
  TraceEventOut,
} from './generated/model';

export type ChatFrame =
  | { type: 'trace'; event: TraceEventOut }
  | { type: 'citations'; citations: CitationOut[] }
  | { type: 'delta'; text: string }
  | {
      type: 'finished';
      budget: BudgetOut;
      degraded: string[];
      branched: boolean;
    }
  | { type: 'error'; message: string };

export interface ChatStreamRequest {
  question: string;
  sessionId?: string;
  modelId?: string;
  sourceIds?: string[];
  history?: [string, string][];
}

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api';

/** Parse one raw SSE block into a typed frame, or null if unrecognised. */
export const parseFrame = (block: string): ChatFrame | null => {
  let event = '';
  const dataLines: string[] = [];

  // Normalised here too, not only in streamChat's buffer handling, so the
  // function is correct when called on its own -- e.g. in a test, or a future
  // caller that has not already normalised. A \r\n split exactly at a chunk
  // boundary is streamChat's problem to solve (it owns the buffer across
  // reads); a lone \r *within* an already-complete block has nothing left to
  // wait for, so normalising it unconditionally here is safe.
  for (const line of block.replace(/\r\n|\r/g, '\n').split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    // Multi-line data is joined with newlines, per the SSE spec — a JSON
    // payload containing a newline would otherwise be truncated.
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  }
  if (!event || dataLines.length === 0) return null;

  const raw = dataLines.join('\n');
  let payload: unknown;
  try {
    payload = JSON.parse(raw);
  } catch {
    return null;
  }

  switch (event) {
    case 'trace':
      return { type: 'trace', event: payload as TraceEventOut };
    case 'citations':
      return { type: 'citations', citations: payload as CitationOut[] };
    case 'delta':
      return { type: 'delta', text: (payload as { text: string }).text };
    case 'finished': {
      const p = payload as {
        budget: BudgetOut;
        degraded: string[];
        branched: boolean;
      };
      return { type: 'finished', ...p };
    }
    case 'error':
      return {
        type: 'error',
        message: (payload as { message: string }).message,
      };
    default:
      return null;
  }
};

/**
 * POST a question and yield typed frames as they arrive.
 *
 * Frames are separated by a blank line. The buffer is only split on a complete
 * separator, so a frame straddling two network chunks is held rather than
 * parsed in half — the failure that would otherwise show up as intermittently
 * dropped citations under load.
 */
export async function* streamChat(
  request: ChatStreamRequest,
  signal?: AbortSignal,
): AsyncGenerator<ChatFrame> {
  const response = await fetch(`${BASE_URL}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      question: request.question,
      session_id: request.sessionId ?? null,
      model_id: request.modelId ?? null,
      source_ids: request.sourceIds ?? null,
      history: request.history ?? [],
    }),
    ...(signal ? { signal } : {}),
  });

  if (!response.ok || !response.body) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    yield { type: 'error', message: detail };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  // Set when the buffer's final character was a lone \r that has not yet been
  // normalised, because it might be the first half of a \r\n pair whose \n
  // arrives in the *next* chunk. Normalising it immediately -- as a first
  // attempt at this did -- commits to "lone \r line ending" before that is
  // known, and turns an ordinary line break into a spurious blank-line
  // separator the instant the next chunk's leading \n is appended. This
  // mirrors how `TextDecoder({ stream: true })` withholds a split multi-byte
  // UTF-8 sequence until the byte that completes it arrives.
  let pendingCR = false;

  const normalise = (done: boolean) => {
    if (pendingCR) {
      buffer = '\r' + buffer;
      pendingCR = false;
    }
    // The SSE spec permits \r\n, lone \r, or \n as a line terminator, and a
    // server is free to use any of them -- sse-starlette sends \r\n.
    if (!done && buffer.endsWith('\r')) {
      buffer = buffer.slice(0, -1);
      pendingCR = true;
    }
    buffer = buffer.replace(/\r\n|\r/g, '\n');
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      normalise(false);

      let separator = buffer.indexOf('\n\n');
      while (separator !== -1) {
        const frame = parseFrame(buffer.slice(0, separator));
        buffer = buffer.slice(separator + 2);
        if (frame) yield frame;
        separator = buffer.indexOf('\n\n');
      }
    }
    // Stream finished: any withheld \r was never going to be joined by a \n,
    // so it is now safe -- the only safe time -- to normalise it as a line
    // ending in its own right.
    normalise(true);
    // A final frame with no trailing blank line still counts.
    const tail = parseFrame(buffer);
    if (tail) yield tail;
  } finally {
    reader.releaseLock();
  }
}
