# The shared output contract (see infra/CONTRACT.md).

output "database_url" {
  description = "Ready for RAGOOGLE_DATABASE_URL. Held in Secrets Manager; this is the reference."
  value       = aws_secretsmanager_secret.database_url.arn
  sensitive   = true
}

output "api_url" {
  description = "Public API endpoint."
  value       = "https://${aws_lb.this.dns_name}"
}

output "frontend_url" {
  description = "Chat UI."
  value       = "https://${aws_lb.this.dns_name}"
}

output "observability_url" {
  description = "Architecture app."
  value       = "https://${aws_lb.this.dns_name}/architecture"
}

output "credential_key_id" {
  description = "KMS key backing credential encryption (ADR-0003)."
  value       = aws_kms_key.this.arn
}

output "document_bucket" {
  description = "Object store for raw document snapshots."
  value       = aws_s3_bucket.documents.id
}

output "telemetry_endpoint" {
  description = "CloudWatch log group receiving the traces from ADR-0009."
  value       = aws_cloudwatch_log_group.this.name
}
