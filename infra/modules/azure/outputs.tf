# The shared output contract (see infra/CONTRACT.md). Every per-cloud module
# emits these, so application configuration is generated identically regardless
# of target.

output "database_url" {
  description = "Ready for RAGOOGLE_DATABASE_URL. The password is in Key Vault, not here."
  value       = "postgresql+asyncpg://ragoogle@${azurerm_postgresql_flexible_server.this.fqdn}:5432/ragoogle"
  sensitive   = true
}

output "api_url" {
  description = "Public API endpoint."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "frontend_url" {
  description = "Chat UI."
  value       = "https://${azurerm_container_app.frontend.ingress[0].fqdn}"
}

output "observability_url" {
  description = "Architecture app."
  value       = "https://${azurerm_container_app.observability.ingress[0].fqdn}"
}

output "credential_key_id" {
  description = "Key Vault secret backing credential encryption (ADR-0003)."
  value       = azurerm_key_vault_secret.credential_secret.versionless_id
}

output "document_bucket" {
  description = "Object store for raw document snapshots."
  value       = azurerm_storage_container.documents.name
}

output "telemetry_endpoint" {
  description = "OTLP-compatible sink for the traces in ADR-0009."
  value       = azurerm_application_insights.this.connection_string
  sensitive   = true
}
