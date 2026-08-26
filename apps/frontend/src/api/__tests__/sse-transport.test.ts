/**
 * Transport-level tests for streamChat.
 *
 * parseFrame.test.ts covers the pure parser against pre-split strings, which is
 * exactly what let a real CRLF bug through undetected: production sends \r\n,
 * and \r\n\r\n contains no literal "\n\n" substring, so the frame-separator scan
 * never matched a single boundary and every frame was silently dropped -- no
 * throw, no error frame, just zero output. These drive the real ReadableStream
 * path with a mocked fetch, matching what the server actually sends on the
 * wire and what got missed before.
 */

import { describe, expect, it, vi } from 'vitest';
import { streamChat } from '../sse';

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i < chunks.length) {
        controller.enqueue(encoder.encode(chunks[i]));
        i++;
      } else {
        controller.close();
      }
    },
  });
}

function mockResponse(body: ReadableStream<Uint8Array>, ok = true) {
  return { ok, status: 200, body, json: async () => ({}) } as Response;
}

async function collect(chunks: string[]) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => mockResponse(streamOf(chunks))),
  );
  const frames = [];
  for await (const frame of streamChat({ question: 'x' })) frames.push(frame);
  vi.unstubAllGlobals();
  return frames;
}

describe('streamChat over a real ReadableStream', () => {
  it('parses CRLF-terminated frames delivered in one chunk', async () => {
    const frames = await collect([
      'event: trace\r\ndata: {"stage":"fusion"}\r\n\r\n' +
        'event: delta\r\ndata: {"text":"hi"}\r\n\r\n',
    ]);
    expect(frames).toEqual([
      { type: 'trace', event: { stage: 'fusion' } },
      { type: 'delta', text: 'hi' },
    ]);
  });

  it('parses CRLF frames split across multiple network chunks', async () => {
    // Mirrors what nginx actually delivered: an 8KB chunk, then a 1.6KB chunk,
    // with the boundary falling inside a frame rather than between two.
    const full =
      'event: trace\r\ndata: {"stage":"a"}\r\n\r\n' +
      'event: trace\r\ndata: {"stage":"b"}\r\n\r\n' +
      'event: finished\r\ndata: {"budget":{},"degraded":[],"branched":false}\r\n\r\n';
    const mid = Math.floor(full.length / 2);
    const frames = await collect([full.slice(0, mid), full.slice(mid)]);
    expect(frames.map((f) => f.type)).toEqual(['trace', 'trace', 'finished']);
  });

  it('does not corrupt a frame when \\r\\n straddles two chunks at the boundary', async () => {
    // The exact edge case per-chunk normalisation would get wrong: chunk one
    // ends on the \r, chunk two begins with the \n, in the middle of an
    // ordinary line -- not at a frame separator.
    const chunks = ['event: delta\r', '\ndata: {"text":"ok"}\r\n\r\n'];
    const frames = await collect(chunks);
    expect(frames).toEqual([{ type: 'delta', text: 'ok' }]);
  });

  it('parses LF-only frames unchanged (no regression for a plainer server)', async () => {
    const frames = await collect(['event: delta\ndata: {"text":"lf"}\n\n']);
    expect(frames).toEqual([{ type: 'delta', text: 'lf' }]);
  });

  it('yields an error frame when the response is not ok', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => mockResponse(streamOf([]), false)));
    const frames = [];
    for await (const frame of streamChat({ question: 'x' })) frames.push(frame);
    vi.unstubAllGlobals();
    expect(frames[0]?.type).toBe('error');
  });
});
