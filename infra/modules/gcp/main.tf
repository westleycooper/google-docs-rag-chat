locals {
  labels = merge(
    {
      application = "ragoogle"
      environment = var.environment
      managed_by  = "terraform"
    },
    var.tags,
  )
  prefix = "${var.name}-${var.environment}"
}

resource "random_password" "postgres" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "random_password" "credential_secret" {
  # Backs RAGOOGLE_CREDENTIAL_SECRET (ADR-0003).
  length  = 64
  special = false
}

# Cloud SQL private IP requires an explicit VPC peering; the default network
# cannot do it, and the failure is an opaque "instance creation failed".
resource "google_compute_network" "this" {
  name                    = "vpc-${local.prefix}"
  auto_create_subnetworks = false
}

resource "google_compute_global_address" "private_ip" {
  name          = "psa-${local.prefix}"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.this.id
}

resource "google_service_networking_connection" "this" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip.name]
}

# ── database ────────────────────────────────────────────────────────────

resource "google_sql_database_instance" "this" {
  name                = "sql-${local.prefix}"
  database_version    = "POSTGRES_${var.postgres_version}"
  region              = var.location
  deletion_protection = var.environment == "prod"

  depends_on = [google_service_networking_connection.this]

  settings {
    tier              = var.postgres_tier
    availability_type = var.environment == "prod" ? "REGIONAL" : "ZONAL"
    disk_size         = var.postgres_storage_gb
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.this.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
    }

    # pgvector is available on Cloud SQL but the extension must still be created
    # by the migration; this only ensures it is permitted (ADR-0011).
    database_flags {
      name  = "cloudsql.enable_pgaudit"
      value = "on"
    }

    user_labels = local.labels
  }
}

resource "google_sql_database" "this" {
  name     = "ragoogle"
  instance = google_sql_database_instance.this.name
}

resource "google_sql_user" "this" {
  name     = "ragoogle"
  instance = google_sql_database_instance.this.name
  password = random_password.postgres.result
}

# ── keys and secrets ────────────────────────────────────────────────────

resource "google_kms_key_ring" "this" {
  name     = "kr-${local.prefix}"
  location = var.location
}

resource "google_kms_crypto_key" "this" {
  name     = "key-${local.prefix}"
  key_ring = google_kms_key_ring.this.id

  rotation_period = "7776000s" # 90 days

  lifecycle {
    # Destroying a key makes every stored Drive credential permanently
    # undecryptable, which is not something a `terraform destroy` should be able
    # to do by accident.
    prevent_destroy = true
  }
}

resource "google_secret_manager_secret" "credential_secret" {
  secret_id = "${local.prefix}-credential-secret"
  labels    = local.labels

  replication {
    auto {
      customer_managed_encryption {
        kms_key_name = google_kms_crypto_key.this.id
      }
    }
  }
}

resource "google_secret_manager_secret_version" "credential_secret" {
  secret      = google_secret_manager_secret.credential_secret.id
  secret_data = random_password.credential_secret.result
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "${local.prefix}-database-url"
  labels    = local.labels

  replication {
    auto {
      customer_managed_encryption {
        kms_key_name = google_kms_crypto_key.this.id
      }
    }
  }
}

resource "google_secret_manager_secret_version" "database_url" {
  secret = google_secret_manager_secret.database_url.id
  secret_data = format(
    "postgresql+asyncpg://ragoogle:%s@%s:5432/ragoogle",
    random_password.postgres.result,
    google_sql_database_instance.this.private_ip_address,
  )
}

# ── object storage ──────────────────────────────────────────────────────

resource "google_storage_bucket" "documents" {
  name                        = "${local.prefix}-documents"
  location                    = var.location
  uniform_bucket_level_access = true
  force_destroy               = var.environment != "prod"
  labels                      = local.labels

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = google_kms_crypto_key.this.id
  }
}

# ── identity ────────────────────────────────────────────────────────────

resource "google_service_account" "api" {
  account_id   = substr("sa-${local.prefix}-api", 0, 30)
  display_name = "RAGDrive API"
}

resource "google_secret_manager_secret_iam_member" "credential_secret" {
  secret_id = google_secret_manager_secret.credential_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_secret_manager_secret_iam_member" "database_url" {
  secret_id = google_secret_manager_secret.database_url.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

resource "google_storage_bucket_iam_member" "documents" {
  bucket = google_storage_bucket.documents.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "trace" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# ── container runtime ───────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "api" {
  name     = "run-${local.prefix}-api"
  location = var.location
  labels   = local.labels

  deletion_protection = var.environment == "prod"
  ingress             = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email

    scaling {
      min_instance_count = var.min_replicas
      max_instance_count = var.max_replicas
    }

    vpc_access {
      network_interfaces {
        network = google_compute_network.this.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.api_image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
        # SSE streams keep a request open for the length of an answer; without
        # CPU outside request processing the stream stalls between tokens.
        cpu_idle = false
      }

      env {
        name  = "RAGOOGLE_EMBEDDING_DIMENSIONS"
        value = tostring(var.embedding_dimensions)
      }
      env {
        name  = "GCS_DOCUMENT_BUCKET"
        value = google_storage_bucket.documents.name
      }
      env {
        name = "RAGOOGLE_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "RAGOOGLE_CREDENTIAL_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.credential_secret.secret_id
            version = "latest"
          }
        }
      }

      # Liveness only; a database blip must not restart a healthy revision.
      liveness_probe {
        http_get {
          path = "/live"
        }
        initial_delay_seconds = 20
        period_seconds        = 15
      }

      startup_probe {
        http_get {
          path = "/health"
        }
        failure_threshold = 10
        period_seconds    = 5
      }
    }

    # An answer can take a couple of minutes to stream; the 5-minute default
    # would cut long ones off mid-sentence.
    timeout = "600s"
  }

  depends_on = [google_secret_manager_secret_version.database_url]
}

resource "google_cloud_run_v2_service" "frontend" {
  name     = "run-${local.prefix}-web"
  location = var.location
  labels   = local.labels

  deletion_protection = var.environment == "prod"

  template {
    scaling {
      min_instance_count = 1
      max_instance_count = 3
    }
    containers {
      image = var.frontend_image
      ports {
        container_port = 80
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }
}

resource "google_cloud_run_v2_service" "observability" {
  name     = "run-${local.prefix}-obs"
  location = var.location
  labels   = local.labels

  deletion_protection = var.environment == "prod"

  template {
    scaling {
      min_instance_count = 1
      max_instance_count = 2
    }
    containers {
      image = var.observability_image
      ports {
        container_port = 80
      }
      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "frontend_public" {
  name     = google_cloud_run_v2_service.frontend.name
  location = google_cloud_run_v2_service.frontend.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
