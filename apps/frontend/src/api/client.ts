/**
 * The fetch mutator orval generates every hook against.
 *
 * One place for base URL, error shape and credentials, so the generated code
 * stays free of environment concerns and regenerating cannot clobber them.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api';

/** An API error carrying the status and the server's `detail` message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
    this.name = 'ApiError';
  }
}

/**
 * The shape orval's generated code calls with.
 *
 * `| undefined` is explicit on every optional field rather than relying on `?`,
 * because `exactOptionalPropertyTypes` is on and the generated code passes
 * `signal: undefined` literally rather than omitting the key. Loosening this
 * here is the honest fix; loosening the compiler flag would hide the same class
 * of bug everywhere else in the app.
 */
export interface RequestConfig {
  url: string;
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  params?: Record<string, unknown> | undefined;
  data?: unknown;
  signal?: AbortSignal | undefined;
  headers?: Record<string, string> | undefined;
}

export const apiRequest = async <T>({
  url,
  method,
  params,
  data,
  signal,
  headers,
}: RequestConfig): Promise<T> => {
  const query = params
    ? `?${new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null)
          .map(([k, v]) => [k, String(v)]),
      )}`
    : '';

  const response = await fetch(`${BASE_URL}${url}${query}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
    ...(data !== undefined ? { body: JSON.stringify(data) } : {}),
    ...(signal ? { signal } : {}),
  });

  if (!response.ok) {
    // FastAPI puts the message in `detail`; falling back to statusText keeps
    // the error legible when a proxy or gateway answers instead of the API.
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body — statusText is the best available */
    }
    throw new ApiError(response.status, detail);
  }

  // 204 has no body; parsing it would throw.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
};

export default apiRequest;
