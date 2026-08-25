# The shared output contract (see infra/CONTRACT.md).

output "database_url" {
  description = "Secret Manager reference for RAGOOGLE_DATABASE_URL."
  value       = google_secret_manager_secret.database_url.id
  sensitive   = true
}

output "api_url" {
  description = "Public API endpoint."
  value       = google_cloud_run_v2_service.api.uri
}

output "frontend_url" {
  description = "Chat UI."
  value       = google_cloud_run_v2_service.frontend.uri
}

output "observability_url" {
  description = "Architecture app."
  value       = google_cloud_run_v2_service.observability.uri
}

output "credential_key_id" {
  description = "Cloud KMS key backing credential encryption (ADR-0003)."
  value       = google_kms_crypto_key.this.id
}

output "document_bucket" {
  description = "Object store for raw document snapshots."
  value       = google_storage_bucket.documents.name
}

output "telemetry_endpoint" {
  description = "Cloud Trace receives the traces from ADR-0009 via the agent role."
  value       = "cloudtrace.googleapis.com"
}
