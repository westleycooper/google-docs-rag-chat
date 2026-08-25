import { describe, expect, it } from 'vitest';
import { parseFrame } from '../sse';

describe('parseFrame', () => {
  it('parses a delta frame', () => {
    const frame = parseFrame('event: delta\ndata: {"text":"hello "}');
    expect(frame).toEqual({ type: 'delta', text: 'hello ' });
  });

  it('parses a citations frame as an array', () => {
    const frame = parseFrame('event: citations\ndata: [{"title":"Q3"}]');
    expect(frame?.type).toBe('citations');
    expect(frame).toMatchObject({ citations: [{ title: 'Q3' }] });
  });

  it('parses a finished frame with its budget', () => {
    const frame = parseFrame(
      'event: finished\ndata: {"budget":{"used_tokens":10},"degraded":["x"],"branched":true}',
    );
    expect(frame).toMatchObject({
      type: 'finished',
      degraded: ['x'],
      branched: true,
    });
  });

  it('parses an error frame', () => {
    const frame = parseFrame('event: error\ndata: {"message":"boom"}');
    expect(frame).toEqual({ type: 'error', message: 'boom' });
  });

  it('joins multi-line data, per the SSE spec', () => {
    // Pretty-printed JSON arrives as several data lines. A raw newline inside a
    // JSON *string* is invalid JSON, so this is what multi-line data actually
    // looks like -- and joining with newlines is what makes it parse.
    const frame = parseFrame(
      'event: delta\ndata: {\ndata:   "text": "hello"\ndata: }',
    );
    expect(frame).toEqual({ type: 'delta', text: 'hello' });
  });

  it('does not lose an escaped newline inside a value', () => {
    const frame = parseFrame('event: delta\ndata: {"text":"a\\nb"}');
    expect(frame).toEqual({ type: 'delta', text: 'a\nb' });
  });

  it('tolerates the optional space after the colon', () => {
    expect(parseFrame('event:delta\ndata:{"text":"x"}')).toEqual({
      type: 'delta',
      text: 'x',
    });
  });

  it('returns null for an unknown event name', () => {
    expect(parseFrame('event: gossip\ndata: {}')).toBeNull();
  });

  it('returns null rather than throwing on malformed JSON', () => {
    // A truncated frame must not take down the whole stream.
    expect(parseFrame('event: delta\ndata: {"text":')).toBeNull();
  });

  it('returns null when there is no data line', () => {
    expect(parseFrame('event: delta')).toBeNull();
  });

  it('returns null for an empty block', () => {
    expect(parseFrame('')).toBeNull();
  });

  it('ignores SSE comment lines', () => {
    expect(parseFrame(': keep-alive\nevent: delta\ndata: {"text":"x"}')).toEqual({
      type: 'delta',
      text: 'x',
    });
  });
});
