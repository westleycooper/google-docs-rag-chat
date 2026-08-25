locals {
  tags = merge(
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
  length  = 32
  special = true
  # Azure rejects several punctuation characters in a Postgres password, and the
  # failure surfaces as an opaque provisioning error rather than a validation
  # message.
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "random_password" "credential_secret" {
  # Backs RAGOOGLE_CREDENTIAL_SECRET, which encrypts Drive credentials at rest
  # (ADR-0003). Generated here and stored in Key Vault so it never passes
  # through a tfvars file or an operator's terminal.
  length  = 64
  special = false
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.prefix}"
  location = var.location
  tags     = local.tags
}

# ── networking ──────────────────────────────────────────────────────────
# Postgres is reachable only from the container subnet. A managed database on a
# public endpoint is one credential leak from a corpus disclosure.

resource "azurerm_virtual_network" "this" {
  name                = "vnet-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  address_space       = ["10.20.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "apps" {
  name                 = "snet-apps"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  # Container Apps requires a /23 or larger for its infrastructure subnet.
  address_prefixes = ["10.20.0.0/23"]
}

resource "azurerm_subnet" "postgres" {
  name                 = "snet-postgres"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.20.2.0/24"]

  delegation {
    name = "postgres"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_private_dns_zone" "postgres" {
  name                = "${local.prefix}.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "postgres" {
  name                  = "link-postgres"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.postgres.name
  virtual_network_id    = azurerm_virtual_network.this.id
  tags                  = local.tags
}

# ── database ────────────────────────────────────────────────────────────

resource "azurerm_postgresql_flexible_server" "this" {
  name                = "psql-${local.prefix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  version             = var.postgres_version

  administrator_login    = "ragoogle"
  administrator_password = random_password.postgres.result

  sku_name   = var.postgres_sku
  storage_mb = var.postgres_storage_mb

  delegated_subnet_id           = azurerm_subnet.postgres.id
  private_dns_zone_id           = azurerm_private_dns_zone.postgres.id
  public_network_access_enabled = false

  backup_retention_days = 14
  zone                  = "1"

  tags = local.tags

  depends_on = [azurerm_private_dns_zone_virtual_network_link.postgres]

  lifecycle {
    # Azure refuses to shrink storage, so a lowered value fails at apply time
    # after the plan looked fine.
    prevent_destroy = false
  }
}

resource "azurerm_postgresql_flexible_server_database" "this" {
  name      = "ragoogle"
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# pgvector is not enabled by default. Without this the first migration fails at
# CREATE EXTENSION with a permissions error that reads like a credential
# problem (ADR-0011).
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = "VECTOR,PG_TRGM"
}

# ── secrets and keys ────────────────────────────────────────────────────

data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "this" {
  name                = substr(replace("kv-${local.prefix}", "-", ""), 0, 24)
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  sku_name            = "standard"

  # Recovery, not convenience: purge protection is what stops an accidental
  # delete of the credential key making every stored Drive credential
  # permanently undecryptable.
  purge_protection_enabled   = true
  soft_delete_retention_days = 30
  rbac_authorization_enabled = true

  tags = local.tags
}

resource "azurerm_key_vault_secret" "credential_secret" {
  name         = "ragoogle-credential-secret"
  value        = random_password.credential_secret.result
  key_vault_id = azurerm_key_vault.this.id
}

resource "azurerm_key_vault_secret" "database_password" {
  name         = "ragoogle-postgres-password"
  value        = random_password.postgres.result
  key_vault_id = azurerm_key_vault.this.id
}

# ── object storage ──────────────────────────────────────────────────────

resource "azurerm_storage_account" "this" {
  name                     = substr(replace("st${local.prefix}", "-", ""), 0, 24)
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  tags = local.tags
}

resource "azurerm_storage_container" "documents" {
  name                  = "documents"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

# ── telemetry ───────────────────────────────────────────────────────────

resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_application_insights" "this" {
  name                = "appi-${local.prefix}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "web"
  tags                = local.tags
}

# ── container runtime ───────────────────────────────────────────────────

resource "azurerm_container_app_environment" "this" {
  name                       = "cae-${local.prefix}"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  infrastructure_subnet_id   = azurerm_subnet.apps.id
  tags                       = local.tags
}

resource "azurerm_user_assigned_identity" "api" {
  name                = "id-${local.prefix}-api"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

# Reading secrets is the only vault permission the API needs; it never writes.
resource "azurerm_role_assignment" "api_vault" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.api.principal_id
}

resource "azurerm_container_app" "api" {
  name                         = "ca-${local.prefix}-api"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.api.id]
  }

  secret {
    name                = "database-url"
    key_vault_secret_id = azurerm_key_vault_secret.database_password.versionless_id
    identity            = azurerm_user_assigned_identity.api.id
  }

  secret {
    name                = "credential-secret"
    key_vault_secret_id = azurerm_key_vault_secret.credential_secret.versionless_id
    identity            = azurerm_user_assigned_identity.api.id
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "api"
      image  = var.api_image
      cpu    = 1.0
      memory = "2Gi"

      env {
        name  = "RAGOOGLE_DATABASE_URL"
        value = "postgresql+asyncpg://ragoogle@${azurerm_postgresql_flexible_server.this.fqdn}:5432/ragoogle"
      }
      env {
        name        = "RAGOOGLE_CREDENTIAL_SECRET"
        secret_name = "credential-secret"
      }
      env {
        name  = "RAGOOGLE_EMBEDDING_DIMENSIONS"
        value = tostring(var.embedding_dimensions)
      }
      env {
        name  = "RAGOOGLE_OTEL_ENDPOINT"
        value = azurerm_application_insights.this.connection_string
      }

      # Liveness only. Probing the database here would restart a healthy API
      # during a brief Postgres blip, turning a partial outage into a total one.
      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/live"
      }

      readiness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/health"
      }
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

resource "azurerm_container_app" "frontend" {
  name                         = "ca-${local.prefix}-web"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  tags                         = local.tags

  template {
    min_replicas = 1
    max_replicas = 3

    container {
      name   = "frontend"
      image  = var.frontend_image
      cpu    = 0.25
      memory = "0.5Gi"
    }
  }

  ingress {
    external_enabled = true
    target_port      = 80

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}

resource "azurerm_container_app" "observability" {
  name                         = "ca-${local.prefix}-obs"
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  tags                         = local.tags

  template {
    min_replicas = 1
    max_replicas = 2

    container {
      name   = "observability"
      image  = var.observability_image
      cpu    = 0.25
      memory = "0.5Gi"
    }
  }

  ingress {
    external_enabled = true
    target_port      = 80

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
