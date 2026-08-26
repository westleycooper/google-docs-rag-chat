/**
 * Topology polling.
 *
 * A deliberately small hand-written client rather than the generated one: this
 * app depends on exactly two endpoints, and pulling in orval, React Query and
 * the whole generated surface to call them would be more machinery than the
 * feature.
 *
 * The types mirror `ComponentNode` and `TopologyResponse` in the API's schemas.
 */

export type NodeStatus = 'ok' | 'degraded' | 'down' | 'unknown';
export type NodeKind = 'service' | 'datastore' | 'external' | 'frontend';

export interface ComponentNode {
  id: string;
  label: string;
  kind: NodeKind;
  status: NodeStatus;
  latency_ms: number | null;
  depends_on: string[];
  adr_refs: string[];
  /** Where a human can go look at this component. Null when nothing is
   * meaningfully clickable (e.g. Postgres). */
  url: string | null;
  /** False marks a documentation-only node (e.g. infra, tooling) with no
   * running process to poll -- its status is always 'unknown', but that is a
   * structural fact, not a failed check. */
  checkable: boolean;
}

export interface Topology {
  nodes: ComponentNode[];
  generated_at: string;
}

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api';

/**
 * Fetch the topology, or report the API itself as down.
 *
 * A failed poll is not an empty topology — that would blank the screen at
 * exactly the moment an operator most needs to see something. The static shape
 * is retained and every node it cannot vouch for goes unknown, with the `api`
 * node marked down, which is the honest reading of "the API did not answer".
 */
export const fetchTopology = async (
  previous: Topology | null,
): Promise<Topology> => {
  try {
    const response = await fetch(`${BASE_URL}/topology`, {
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return (await response.json()) as Topology;
  } catch {
    if (!previous) {
      return {
        nodes: [
          {
            id: 'api',
            label: 'API',
            kind: 'service',
            status: 'down',
            latency_ms: null,
            depends_on: [],
            adr_refs: [],
            url: null,
            checkable: true,
          },
        ],
        generated_at: new Date().toISOString(),
      };
    }
    return {
      ...previous,
      nodes: previous.nodes.map((node) => ({
        ...node,
        status: node.id === 'api' ? 'down' : 'unknown',
        latency_ms: null,
      })),
      generated_at: new Date().toISOString(),
    };
  }
};
