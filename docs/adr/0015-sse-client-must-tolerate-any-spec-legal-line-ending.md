---
id: 0015
title: The SSE client must tolerate any spec-legal line ending, not just LF
status: accepted
date: 2026-08-26
deciders: [Westley Cooper-Thorn]
component: frontend
tags: [sse, streaming, bug, testing]
supersedes: []
superseded_by: []
---

# ADR-0015: The SSE client must tolerate any spec-legal line ending, not just LF

## Context

Every chat turn silently hung. The user saw a message bubble stuck at "0
steps," an endless spinner, and no error — forever. Chrome DevTools' Network
panel showed the *raw response* arriving correctly: real trace events, real
citations, a real streamed answer, all well-formed. The transport worked. The
UI simply never moved.

That combination — good bytes on the wire, nothing reaching the store — pointed
at the client's own parser rather than the server, the proxy, or React. `git
diff`-driven review couldn't find it, because the parser's unit tests
([`sse.test.ts`](../../apps/frontend/src/api/__tests__/sse.test.ts)) all
passed: they fed `parseFrame` pre-built strings like
`'event: delta\ndata: {"text":"hi"}'` and asserted the frame came out right.
Reproducing against the real system (`tools/ingest/local.py`'s corpus, a real
question, curl through the same nginx path the browser uses, then a
standalone Node script running the *exact* client algorithm against a live
`fetch`) surfaced it in minutes: the response's raw bytes used `\r\n` line
endings, not `\n`.

`sse-starlette` (the API's SSE server) sends `\r\n`. The client's frame
separator search was `buffer.indexOf('\n\n')`. `\r\n\r\n` — the actual blank
line between two SSE records under CRLF — contains no literal `\n\n`
substring. The search never matched. Every frame the server ever sent was
concatenated into one ever-growing buffer that was never split, so
`parseFrame` was never called on a valid single-frame block during the whole
stream, and the stream's `for await` loop finished having yielded nothing.

Every earlier "successful" end-to-end test in this project's history had
gone through a Python-based harness — `curl | python3` — for reading the SSE
lines. Python's text-mode line iteration performs universal-newline
translation automatically, silently treating `\r\n` as an ordinary line break.
That is precisely why no manual test caught this: the checking tool tolerated
the exact thing the shipped client did not.

The SSE specification is explicit that a line may be terminated by `\r\n`,
a lone `\r`, or a lone `\n`, and a conformant server may use any of them. A
client that assumes `\n` is not a client that handles an edge case wrong; it
is a client that implements a narrower protocol than the one it claims to
speak.

## Decision

`streamChat`'s buffer handling and `parseFrame` both normalise line endings
to `\n` before doing any `\n`-based parsing, and both are covered by tests
that exercise the failure mode directly rather than only the parser's happy
path.

Two places, deliberately:

- **`streamChat`** normalises the *accumulated buffer* on each read, because
  it is the one component that sees reads arrive in arbitrary pieces and must
  find frame boundaries across them.
- **`parseFrame`** normalises its own input independently, so the function is
  correct when called on its own — in a test, or by a future caller that has
  not already normalised anything.

The first implementation of the buffer fix was itself wrong, and the way it
was wrong is the more interesting decision to record. Normalising the whole
buffer *inside the read loop, on every iteration*, converts a buffer-trailing
lone `\r` to `\n` immediately — before the next read has had a chance to
supply the `\n` that `\r` was the first half of. When that `\n` arrives, it is
appended after a character that was already turned into `\n`, producing a
spurious `\n\n`: a frame is split in half at an ordinary line ending that
happened to fall across a chunk boundary. A test constructed for exactly this
case (`chunks = ['event: delta\r', '\ndata: {"text":"ok"}\r\n\r\n']`) failed
against the first fix and passed against the second.

The second, correct version withholds a buffer-final lone `\r` — refusing to
normalise it until either the next read resolves it into `\r\n` or the stream
ends and there is nothing left to wait for. This is the same technique
`TextDecoder({ stream: true })` uses internally for a multi-byte UTF-8
sequence split across two chunks: don't commit an interpretation to a
trailing byte until you know what follows it.

## Consequences

### Positive

- The client now implements the transport it claims to speak, and is correct
  regardless of which SSE server library eventually serves it, or whether an
  intermediate proxy rewrites line endings.
- The new tests
  ([`sse-transport.test.ts`](../../apps/frontend/src/api/__tests__/sse-transport.test.ts))
  drive a real `ReadableStream` with chunk boundaries chosen to hit the
  specific split cases that broke — including the boundary the first fix
  attempt got wrong — rather than only a pure function fed a whole string.
- The failure mode this closes is uniquely bad: total silence. No exception,
  no error frame, no console output — the UI simply never updates. Any future
  regression in this area is far more likely to be caught by a test that
  reproduces the exact wire shape than by one that doesn't.

### Negative

- Two normalisation sites (`streamChat`'s buffer, `parseFrame`'s own input)
  do overlapping work on the common path, where `streamChat` has already
  normalised before `parseFrame` ever sees a block. Accepted deliberately:
  the alternative is a `parseFrame` that is only correct when called a
  particular way, which is the same class of implicit, untested assumption
  that produced this bug in the first place.
- The buffer-normalisation logic is now stateful (`pendingCR` held across
  loop iterations) rather than a single expression, which is more to get
  wrong later. The extensive comments and the dedicated straddling-boundary
  test are the mitigation.

### Neutral

- No server-side change. `\r\n` is spec-legal; the bug was never that
  `sse-starlette` did something wrong.

## Alternatives Considered

**Configure the server to emit bare `\n`.** Would have fixed this specific
deployment's specific server library. Rejected because it treats a symptom of
a narrower problem: the client would still break against any other
CRLF-emitting SSE server, any proxy that rewrites line endings, or a future
version of `sse-starlette` that changes its formatting. The bug was in what
the client was willing to accept, not in what this one server happened to
send.

**Use the browser's native `EventSource` instead of hand-rolled SSE
parsing.** Would eliminate this whole class of bug — `EventSource` handles
line-ending variation internally per spec. Rejected, as before: `EventSource`
only issues GET requests and cannot send the JSON body the chat endpoint
requires. Still the reason a hand-rolled parser exists at all, and now also
the reason it has to be this careful.

**Trust that `git diff` review of the parser change would have caught the
first (buggy) fix.** It did not, in practice — the flaw was caught only by
constructing a test for the specific chunk-boundary shape and watching it
fail. Recorded here as the operating lesson: a fix for a boundary-condition
bug needs a test that exercises the boundary, not a description of the fix
that sounds right.
