# The shared input contract (see infra/CONTRACT.md). Names and meanings are
# identical in every per-cloud module so an environment can be re-pointed by
# changing which module it calls.

variable "name" {
  description = "Resource name prefix. Short: Azure caps several name lengths."
  type        = string
  default     = "ragoogle"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,16}$", var.name))
    error_message = "name must be 3-17 lowercase alphanumeric or hyphen characters, starting with a letter."
  }
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "uksouth"
}

variable "environment" {
  description = "Environment name, used in tags and resource names."
  type        = string
  default     = "dev"
}

variable "postgres_version" {
  description = "PostgreSQL major version. 16+ for the pgvector features used."
  type        = string
  default     = "16"

  validation {
    condition     = tonumber(var.postgres_version) >= 16
    error_message = "PostgreSQL 16 or later is required for the HNSW index configuration in ADR-0011."
  }
}

variable "postgres_sku" {
  description = "Flexible Server SKU. HNSW index builds are memory-hungry (ADR-0011)."
  type        = string
  default     = "GP_Standard_D2ds_v4"
}

variable "postgres_storage_mb" {
  description = "Storage in MB. Vectors dominate: ~4KB per chunk at 1024 dims."
  type        = number
  default     = 65536
}

variable "api_image" {
  description = "Fully-qualified API container image."
  type        = string
}

variable "frontend_image" {
  description = "Fully-qualified chat UI container image."
  type        = string
}

variable "observability_image" {
  description = "Fully-qualified observability app container image."
  type        = string
}

variable "embedding_dimensions" {
  description = <<-EOT
    Must match the deployed pgvector column (ADR-0002). Changing this means
    re-embedding the entire corpus, not a redeploy -- the API refuses to start
    when it disagrees with the database.
  EOT
  type        = number
  default     = 1024

  validation {
    condition     = contains([256, 512, 1024, 2048], var.embedding_dimensions)
    error_message = "voyage-3-large supports 256, 512, 1024 or 2048 output dimensions."
  }
}

variable "min_replicas" {
  description = "Minimum API replicas. 0 scales to zero; the first request then pays a cold start."
  type        = number
  default     = 1
}

variable "max_replicas" {
  description = "Maximum API replicas."
  type        = number
  default     = 4
}

variable "tags" {
  description = "Additional tags applied to every resource."
  type        = map(string)
  default     = {}
}
