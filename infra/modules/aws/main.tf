locals {
  tags = merge(
    {
      Application = "ragoogle"
      Environment = var.environment
      ManagedBy   = "terraform"
    },
    var.tags,
  )
  prefix = "${var.name}-${var.environment}"
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "random_password" "postgres" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "random_password" "credential_secret" {
  # Backs RAGOOGLE_CREDENTIAL_SECRET (ADR-0003). Generated here so it never
  # passes through a tfvars file or an operator's terminal.
  length  = 64
  special = false
}

# ── networking ──────────────────────────────────────────────────────────

resource "aws_vpc" "this" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.tags, { Name = "vpc-${local.prefix}" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "igw-${local.prefix}" })
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(aws_vpc.this.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = merge(local.tags, { Name = "snet-${local.prefix}-public-${count.index}" })
}

# RDS requires a subnet group spanning at least two AZs, even for a
# single-AZ instance.
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(aws_vpc.this.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = merge(local.tags, { Name = "snet-${local.prefix}-private-${count.index}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(local.tags, { Name = "rt-${local.prefix}-public" })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "tasks" {
  name        = "sg-${local.prefix}-tasks"
  description = "RAGDrive ECS tasks"
  vpc_id      = aws_vpc.this.id

  egress {
    description = "Outbound to Voyage, Anthropic and Google Drive"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.tags
}

resource "aws_security_group" "postgres" {
  name        = "sg-${local.prefix}-postgres"
  description = "RAGDrive database"
  vpc_id      = aws_vpc.this.id

  ingress {
    description     = "Postgres from the task security group only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.tasks.id]
  }

  tags = local.tags
}

# ── database ────────────────────────────────────────────────────────────

resource "aws_db_subnet_group" "this" {
  name       = "dbsg-${local.prefix}"
  subnet_ids = aws_subnet.private[*].id
  tags       = local.tags
}

# pgvector is available but not enabled by default; the migration's
# CREATE EXTENSION needs rds_superuser, which the master user has (ADR-0011).
resource "aws_db_parameter_group" "this" {
  name   = "pg-${local.prefix}"
  family = "postgres${var.postgres_version}"

  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements"
    apply_method = "pending-reboot"
  }

  tags = local.tags
}

resource "aws_db_instance" "this" {
  identifier     = "rds-${local.prefix}"
  engine         = "postgres"
  engine_version = var.postgres_version
  instance_class = var.postgres_instance_class

  allocated_storage     = var.postgres_storage_gb
  max_allocated_storage = var.postgres_storage_gb * 4
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.this.arn

  db_name  = "ragoogle"
  username = "ragoogle"
  password = random_password.postgres.result

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.postgres.id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false

  backup_retention_period = 14
  skip_final_snapshot     = var.environment != "prod"
  deletion_protection     = var.environment == "prod"

  performance_insights_enabled = true
  tags                         = local.tags
}

# ── keys and secrets ────────────────────────────────────────────────────

resource "aws_kms_key" "this" {
  description         = "RAGDrive: database storage and credential envelope encryption"
  enable_key_rotation = true
  # A deleted key makes every stored Drive credential permanently undecryptable,
  # so the window is the maximum rather than the 7-day default.
  deletion_window_in_days = 30
  tags                    = local.tags
}

resource "aws_kms_alias" "this" {
  name          = "alias/${local.prefix}"
  target_key_id = aws_kms_key.this.key_id
}

resource "aws_secretsmanager_secret" "credential_secret" {
  name       = "${local.prefix}/credential-secret"
  kms_key_id = aws_kms_key.this.id
  tags       = local.tags
}

resource "aws_secretsmanager_secret_version" "credential_secret" {
  secret_id     = aws_secretsmanager_secret.credential_secret.id
  secret_string = random_password.credential_secret.result
}

resource "aws_secretsmanager_secret" "database_url" {
  name       = "${local.prefix}/database-url"
  kms_key_id = aws_kms_key.this.id
  tags       = local.tags
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format(
    "postgresql+asyncpg://ragoogle:%s@%s/ragoogle",
    random_password.postgres.result,
    aws_db_instance.this.endpoint,
  )
}

# ── object storage ──────────────────────────────────────────────────────

resource "aws_s3_bucket" "documents" {
  bucket = "${local.prefix}-documents"
  tags   = local.tags
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.this.arn
    }
  }
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

# ── telemetry ───────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${local.prefix}"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.this.arn
  tags              = local.tags
}

# ── container runtime ───────────────────────────────────────────────────

resource "aws_ecs_cluster" "this" {
  name = "ecs-${local.prefix}"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
  tags = local.tags
}

data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "role-${local.prefix}-exec"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role pulls secrets at task start; the task role is what the
# running application uses. Keeping them separate means a compromised container
# cannot read the secrets it was started with.
data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.credential_secret.arn,
      aws_secretsmanager_secret.database_url.arn,
    ]
  }
  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.this.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "role-${local.prefix}-task"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "task" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.documents.arn}/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.documents.arn]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "documents"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  tags                     = local.tags

  container_definitions = jsonencode([
    {
      name         = "api"
      image        = var.api_image
      essential    = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      environment = [
        { name = "RAGOOGLE_EMBEDDING_DIMENSIONS", value = tostring(var.embedding_dimensions) },
        { name = "AWS_S3_DOCUMENT_BUCKET", value = aws_s3_bucket.documents.id },
      ]
      secrets = [
        { name = "RAGOOGLE_DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
        { name = "RAGOOGLE_CREDENTIAL_SECRET", valueFrom = aws_secretsmanager_secret.credential_secret.arn },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.this.name
          "awslogs-region"        = var.location
          "awslogs-stream-prefix" = "api"
        }
      }
      # /live, not /health: a database blip must not make ECS replace a healthy
      # task and turn a partial outage into a total one.
      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/live').status==204 else 1)\""]
        interval    = 15
        timeout     = 3
        retries     = 3
        startPeriod = 30
      }
    }
  ])
}

resource "aws_lb" "this" {
  name               = substr("alb-${local.prefix}", 0, 32)
  load_balancer_type = "application"
  subnets            = aws_subnet.public[*].id
  security_groups    = [aws_security_group.alb.id]
  tags               = local.tags
}

resource "aws_security_group" "alb" {
  name        = "sg-${local.prefix}-alb"
  description = "RAGDrive load balancer"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.tags
}

resource "aws_lb_target_group" "api" {
  name        = substr("tg-${local.prefix}-api", 0, 32)
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.this.id
  target_type = "ip"

  health_check {
    path                = "/live"
    matcher             = "204"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # SSE streams are long-lived; the default 300s deregistration delay would cut
  # an in-flight answer during a deploy.
  deregistration_delay = 60

  tags = local.tags
}

resource "aws_ecs_service" "api" {
  name            = "svc-${local.prefix}-api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.min_replicas
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  tags = local.tags
}
