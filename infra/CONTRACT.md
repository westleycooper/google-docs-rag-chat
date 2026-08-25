# The cloud contract

Every per-cloud module in `modules/` satisfies the same contract, which is what
makes ADR-0005's portability claim testable rather than aspirational. A cloud
that cannot satisfy a row here fails the contract, rather than quietly forking
the architecture.

## Required capabilities

| Capability | Why | Azure | AWS | GCP |
|---|---|---|---|---|
| Managed PostgreSQL ≥ 16 with the `vector` extension | The corpus and the HNSW index (ADR-0011) | Database for PostgreSQL Flexible Server | RDS for PostgreSQL | Cloud SQL for PostgreSQL |
| Container runtime with scale-to-N | API and both UIs | Container Apps | ECS Fargate | Cloud Run |
| KMS-managed key | Envelope encryption for Drive credentials (ADR-0003) | Key Vault | KMS | Cloud KMS |
| Object store | Raw document snapshots | Blob Storage | S3 | Cloud Storage |
| Secret store | API keys, never in an image or a tfvars file | Key Vault | Secrets Manager | Secret Manager |
| OTLP-compatible telemetry sink | Traces from ADR-0009 | Application Insights | CloudWatch / ADOT | Cloud Trace |

`pg_search` (real BM25) is deliberately **not** on this list: it is unavailable
on all three managed offerings, which is exactly why ADR-0012 settles on
`ts_rank_cd` instead.

## Required inputs

Every module takes the same variables — see `modules/*/variables.tf`. The names
and meanings are identical across clouds so an environment can be re-pointed by
changing which module it calls.

## Required outputs

Every module emits the same outputs, so the application's environment
configuration is generated identically regardless of target:

- `database_url` (sensitive) — ready for `RAGOOGLE_DATABASE_URL`
- `api_url`, `frontend_url`, `observability_url`
- `credential_key_id` — the KMS key backing `RAGOOGLE_CREDENTIAL_SECRET`
- `document_bucket`
- `telemetry_endpoint`
