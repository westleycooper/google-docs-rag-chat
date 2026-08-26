---
id: 0005
title: Provision RAGDrive with Terraform across Azure, AWS and GCP
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: infra
tags: [terraform, iac, multi-cloud, portability]
supersedes: []
superseded_by: []
---

# ADR-0005: Provision RAGDrive with Terraform across Azure, AWS and GCP

## Context

The platform must be deployable to all three major clouds. That requirement is
what makes several earlier decisions coherent: it is why the default embedding
model is deliberately not the one that would have consolidated billing into GCP
(see [ADR-0002](0002-voyage-3-large-as-default-embedding-model.md)), and it is why
the vector store is Postgres with pgvector — an extension available as a managed
offering on all three — rather than a cloud-native vector service.

The risk in a three-cloud IaC estate is drift: three codebases that diverge until
"deploys to AWS" and "deploys to Azure" mean materially different systems.

## Decision

We will write Terraform as a set of **cloud-agnostic root modules over per-cloud
implementation modules**, with a shared contract each cloud must satisfy:
managed Postgres with the `vector` extension, a container runtime for the API and
frontend, a KMS-backed key for credential envelope encryption
(see [ADR-0003](0003-dual-mode-google-drive-authentication.md)), an object store
for raw document snapshots, and an OTLP-compatible telemetry sink.

Concretely: Azure Database for PostgreSQL Flexible Server + Container Apps + Key
Vault; AWS RDS/Aurora PostgreSQL + ECS Fargate + KMS; Cloud SQL for PostgreSQL +
Cloud Run + Cloud KMS. Each per-cloud module exposes the same output contract, so
the application's environment configuration is generated identically regardless
of target.

Validation runs on a hook watching `infra/**`: `terraform fmt -check`,
`terraform validate` per cloud, and `tflint`. This is deliberately the
fast, credential-free subset — it catches syntax and provider-schema errors on
every change without requiring cloud credentials to be present locally. Plan and
policy checks belong in CI where credentials exist.

## Consequences

### Positive

- No cloud lock-in for a platform whose data (a customer's document corpus) is
  precisely the thing that is expensive to move.
- The shared contract makes divergence visible: a capability one cloud cannot
  satisfy fails the contract rather than quietly forking the architecture.
- Format/validate on save catches the majority of Terraform errors at authoring
  time rather than at apply time.

### Negative

- Three implementations of every piece of infrastructure, and a three-way change
  for anything touching the shared contract. This is the dominant ongoing cost of
  the decision.
- The contract necessarily targets the intersection of three clouds' managed
  services, forgoing genuinely better cloud-specific options.
- `terraform validate` without credentials cannot catch what only a real plan
  catches — IAM shape errors, quota, region availability.

### Neutral

- State backends are per-cloud native (Azure Storage, S3 + DynamoDB, GCS) rather
  than a single centralised backend, since the bootstrap otherwise requires one
  cloud to deploy any of the others.

## Alternatives Considered

**Single cloud, with portability deferred.** Far less work and the honest choice
for most products. Rejected because it was an explicit platform requirement, and
because retrofitting portability after data lands is the expensive direction.

**Pulumi or CDK.** Real programming languages, better abstraction over three
providers, less duplication. Rejected as a smaller and less portable operational
ecosystem for the same job; Terraform's provider coverage and state model are the
more conservative choice for infrastructure meant to outlive the build.

**Kubernetes everywhere via a single Helm chart.** Would genuinely collapse three
runtimes into one contract. Rejected as disproportionate: it trades three managed
container runtimes for one cluster to operate on each of three clouds.
