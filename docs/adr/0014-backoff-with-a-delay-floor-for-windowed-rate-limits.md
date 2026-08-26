---
id: 0014
title: Retry vendor rate limits with a delay floor, not full jitter from zero
status: accepted
date: 2026-08-26
deciders: [Westley Cooper-Thorn]
component: rag-core
tags: [reliability, rate-limits, vendors, backoff]
supersedes: []
superseded_by: []
---

# ADR-0014: Retry vendor rate limits with a delay floor, not full jitter from zero

## Context

RAGDrive makes two Voyage calls per question — one to embed the query, one to
rerank — and one per batch during ingestion. Both vendors rate-limit.

The first implementation failed the whole operation on the first 429. That is
wrong in two different ways depending on which operation it is: an ingestion run
over a real corpus becomes impossible, and a user's question fails for a reason
that had nothing to do with the question.

Adding classic exponential backoff with full jitter — waiting a random interval
in `[0, delay]` — did not fix it. Running against a Voyage free-tier key
(10,000 tokens per minute) the retries were observed failing five times in a row
and giving up, all inside the same rejected minute.

The reason is a property of how these limits are actually enforced, and it is
easy to miss: **a vendor rate limit is usually a budget over a window, not a
request rate.** Once a window's token budget is spent, every retry inside that
window is guaranteed to fail — there is nothing left to consume. Full jitter
from zero spends most of its attempts on calls that cannot possibly succeed, and
then gives up while still inside the window that rejected it.

Classic full jitter is designed for a different failure: contention between many
clients, where retrying sooner is fine as long as they do not retry *together*.
That is a real problem and jitter is the right answer to it. It is simply not
the problem here.

## Decision

We will retry rate limits with exponential backoff, jittered **between a floor
and the ceiling** rather than between zero and the ceiling.

`MIN_DELAY_SECONDS = 20` — long enough that every attempt lands in a fresh
window, short enough that a transient limit does not stall a question for a
minute. Randomising between the floor and the growing ceiling keeps the
thundering-herd protection that made jitter worth having.

The retry is deliberately narrow in what it catches. A rate limit is transient;
a 400 for a malformed request is not, and retrying that turns a clear error into
a slow one.

Both Voyage adapters share one helper (`ragoogle_infra.vendor_retry`) rather
than each carrying a copy, because a retry policy that differs between the
ingest path and the query path is a policy nobody can reason about.

## Consequences

### Positive

- Ingestion survives rate limits instead of failing a run partway and requiring
  a manual resume.
- A throttled question waits rather than erroring, which is what a user expects
  from a system that is merely busy.
- The floor is stated in one place with the reasoning attached, so the next
  person to tune it knows why it is not zero.

### Negative

- A genuinely transient blip now costs at least 20 seconds, where full jitter
  might have recovered in two. This is the deliberate trade: the common case
  here is a spent window, not contention.
- Six attempts from a 30-second base means a persistent limit is tolerated for
  several minutes before failing. For an interactive question that is a long
  time to wait for an eventual error.
- Retries are invisible to the user beyond the delay. The turn's `degraded` list
  reports what the pipeline gave up on, not what it waited for.

### Neutral

- The limits that motivated this are a free-tier artefact. On a paid key the
  floor rarely engages, and it costs nothing when it does not.

## Alternatives Considered

**Full jitter from zero (the textbook default).** Correct for contention between
many clients, and what most guidance recommends. Rejected on evidence: measured
against a real windowed budget it failed every attempt and gave up inside the
rejected window.

**Honour a `Retry-After` header.** Strictly better when the vendor sends one,
since it removes the guess entirely. Rejected for now because the Voyage SDK
surfaces its rate limit as a typed exception that does not expose response
headers; the floor is the best available approximation. Worth revisiting if the
SDK exposes them.

**A client-side token-bucket limiter sized to the plan.** Would avoid the 429
entirely rather than reacting to it, and is the right answer at scale. Rejected
as premature: it needs the plan's limits as configuration, which then has to be
kept in step with the account, and it does not remove the need for a retry when
the estimate is wrong.

**Reduce the work instead — send fewer rerank candidates.** Also done, and
independently correct: a 50-candidate rerank exceeds a free-tier token budget in
a single request, so no retry policy can rescue it. `candidate_limit` is
configuration for exactly this reason. But it is a complement to the retry, not
a substitute — it lowers the cost per call rather than handling a limit that has
already been hit.
