import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchTopology, type Topology } from '../topology';

const healthy: Topology = {
  nodes: [
    { id: 'api', label: 'API', kind: 'service', status: 'ok', latency_ms: 12, depends_on: ['vectorstore'], adr_refs: ['ADR-0001'], url: 'http://localhost:8000/docs', checkable: true },
    { id: 'vectorstore', label: 'Postgres', kind: 'datastore', status: 'ok', latency_ms: null, depends_on: [], adr_refs: ['ADR-0011'], url: null, checkable: true },
  ],
  generated_at: '2026-08-26T00:00:00Z',
};

afterEach(() => vi.unstubAllGlobals());

const stubFetch = (impl: () => Promise<Response>) =>
  vi.stubGlobal('fetch', vi.fn(impl));

describe('fetchTopology', () => {
  it('returns the topology when the API answers', async () => {
    stubFetch(async () => new Response(JSON.stringify(healthy), { status: 200 }));
    const result = await fetchTopology(null);
    expect(result.nodes).toHaveLength(2);
    expect(result.nodes[0]?.status).toBe('ok');
  });

  it('reports the API down on a network failure with no prior state', async () => {
    stubFetch(async () => {
      throw new TypeError('network');
    });
    const result = await fetchTopology(null);
    expect(result.nodes).toHaveLength(1);
    expect(result.nodes[0]).toMatchObject({ id: 'api', status: 'down' });
  });

  it('keeps the last known shape rather than blanking the screen', async () => {
    // A failed poll is not an empty topology -- that would clear the display at
    // exactly the moment an operator most needs to see something.
    stubFetch(async () => {
      throw new TypeError('network');
    });
    const result = await fetchTopology(healthy);
    expect(result.nodes).toHaveLength(2);
    expect(result.nodes.map((n) => n.id)).toEqual(['api', 'vectorstore']);
  });

  it('marks the api down and everything else unknown after a failure', async () => {
    // "The API did not answer" says nothing about Postgres, so claiming it is
    // down would be a guess presented as a fact.
    stubFetch(async () => {
      throw new TypeError('network');
    });
    const result = await fetchTopology(healthy);
    expect(result.nodes.find((n) => n.id === 'api')?.status).toBe('down');
    expect(result.nodes.find((n) => n.id === 'vectorstore')?.status).toBe('unknown');
  });

  it('clears stale latencies on failure', async () => {
    stubFetch(async () => {
      throw new TypeError('network');
    });
    const result = await fetchTopology(healthy);
    expect(result.nodes.every((n) => n.latency_ms === null)).toBe(true);
  });

  it('preserves ADR references through a failure', async () => {
    // Why a component is the way it is does not change because it stopped
    // answering.
    stubFetch(async () => {
      throw new TypeError('network');
    });
    const result = await fetchTopology(healthy);
    expect(result.nodes.find((n) => n.id === 'vectorstore')?.adr_refs).toEqual([
      'ADR-0011',
    ]);
  });

  it('treats a non-2xx response as a failure', async () => {
    stubFetch(async () => new Response('nope', { status: 503 }));
    const result = await fetchTopology(healthy);
    expect(result.nodes.find((n) => n.id === 'api')?.status).toBe('down');
  });

  it('advances the timestamp on a degraded poll', async () => {
    stubFetch(async () => {
      throw new TypeError('network');
    });
    const result = await fetchTopology(healthy);
    expect(result.generated_at).not.toBe(healthy.generated_at);
  });
});
