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

variable "project_id" {
  description = "GCP project id."
  type        = string
}
