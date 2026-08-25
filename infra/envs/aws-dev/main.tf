# Ragoogle on AWS, development.
#
# Every environment is a thin call into a per-cloud module (ADR-0005). Keeping
# the roots this small is what makes the portability claim inspectable: if one
# cloud needed extra resources here, that would be the contract failing.

terraform {
  required_version = ">= 1.9"

  # State backends are per-cloud native rather than centralised, because a
  # single backend would make one cloud a hard dependency of deploying any of
  # the others. Configure with `terraform init -backend-config=...`.
  # backend "s3" {}
}

module "ragoogle" {
  source = "../../modules/aws"

  environment = "dev"

  api_image           = var.api_image
  frontend_image      = var.frontend_image
  observability_image = var.observability_image

}
