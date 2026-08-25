import { defineConfig } from 'orval';

/**
 * Generates TypeScript types and React Query hooks from the committed OpenAPI
 * contract.
 *
 * The input is `contracts/openapi.json` on disk, not a running server. That is
 * deliberate: codegen must work on a clean checkout in CI with no database and
 * no API process, and it makes an accidental contract change show up as a diff
 * in the PR that causes it rather than as a broken build later.
 *
 * The generated directory is committed and checked in CI (`codegen:check`), so
 * the types the frontend compiles against are always the ones the API actually
 * publishes.
 */
export default defineConfig({
  ragoogle: {
    input: {
      target: '../../contracts/openapi.json',
    },
    output: {
      mode: 'tags-split',
      target: 'src/api/generated/ragoogle.ts',
      schemas: 'src/api/generated/model',
      client: 'react-query',
      prettier: false,
      clean: true,
      override: {
        mutator: {
          path: 'src/api/client.ts',
          name: 'apiRequest',
        },
        query: {
          useQuery: true,
          useSuspenseQuery: false,
        },
      },
    },
  },
});
