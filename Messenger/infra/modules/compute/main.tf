data "aws_caller_identity" "current" {}

# Amazon Linux 2023 (arm64) 最新 AMI
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  table_arns = values(var.table_arns)
}

# ---------- ECR ----------
# 單一 image：gateway / pubsub 兩種角色跑同一份映像，靠 ROLE env 區分（見下方兩組 ASG）。
resource "aws_ecr_repository" "app" {
  name                 = var.project
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration {
    scan_on_push = true
  }
}

# IMAGE_URI 由 CI/CD 更新；先放預設 latest tag。
resource "aws_ssm_parameter" "image_uri" {
  name      = "${var.ssm_prefix}/IMAGE_URI"
  type      = "String"
  value     = "${aws_ecr_repository.app.repository_url}:latest"
  overwrite = true
  lifecycle {
    ignore_changes = [value] # CI/CD 會改它，Terraform 不要覆蓋回去
  }
}

# App 設定（開機 / 部署時由 deploy-app.sh 撈進容器；對應 app/config.py）
# ETCD_URL 由 etcd 模組寫入；COGNITO_* 由 auth 模組寫入。
locals {
  app_params = {
    DYNAMO_TABLE_PREFIX = var.table_prefix
    NUM_SHARDS          = tostring(var.num_shards)
    LEASE_TTL_S         = tostring(var.lease_ttl_s)
    MAX_PARTICIPANTS    = tostring(var.max_participants)
    ALLOW_DEBUG         = var.allow_debug ? "1" : "0"
    AUTH_ENABLED        = var.auth_enabled ? "true" : "false"
  }
}

resource "aws_ssm_parameter" "app" {
  for_each  = local.app_params
  name      = "${var.ssm_prefix}/${each.key}"
  type      = "String"
  value     = each.value
  overwrite = true
}

# ---------- IAM (EC2 instance role，gateway / pubsub 共用) ----------
resource "aws_iam_role" "ec2" {
  name_prefix = "${var.project}-ec2-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# 讀取 /<project>/* SSM 參數 + 送 CloudWatch Logs
resource "aws_iam_role_policy" "app_config" {
  name = "${var.project}-app-config"
  role = aws_iam_role.ec2.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${var.ssm_prefix}/*"
      },
      {
        # Monitoring：容器用 awslogs driver 送 CloudWatch Logs
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:aws:logs:${var.region}:${local.account_id}:log-group:/${var.project}/*",
          "arn:aws:logs:${var.region}:${local.account_id}:log-group:/${var.project}/*:*",
        ]
      }
    ]
  })
}

# DynamoDB data-plane only（5 張表 + 其 index）。刻意不給 CreateTable / DeleteTable / UpdateTable：
# 表由 Terraform（data 模組）管，app 的 ensure_tables 只在 DynamoDB Local 才會跑。
resource "aws_iam_role_policy" "dynamo" {
  name = "${var.project}-dynamo"
  role = aws_iam_role.ec2.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:UpdateItem",
        "dynamodb:DeleteItem",
        "dynamodb:BatchWriteItem",
        "dynamodb:BatchGetItem",
        "dynamodb:TransactWriteItems",
        "dynamodb:ConditionCheckItem",
      ]
      Resource = concat(local.table_arns, [for a in local.table_arns : "${a}/index/*"])
    }]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name_prefix = "${var.project}-ec2-"
  role        = aws_iam_role.ec2.name
}

# ---------- gateway target group（ALB 本體在 edge 模組）----------
# WebSocket 走一般 HTTP target group 即可（ALB 原生支援 Upgrade）。
# deregistration_delay 30：縮容 / 換機時給既有連線 30 秒收尾，client 之後自動重連到別台。
resource "aws_lb_target_group" "gateway" {
  name                 = "${var.project}-gw-tg"
  port                 = 8031
  protocol             = "HTTP"
  vpc_id               = var.vpc_id
  target_type          = "instance"
  deregistration_delay = 30

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# ---------- Launch templates（gateway / pubsub：同 AMI、同映像，差在 ROLE）----------
resource "aws_launch_template" "gateway" {
  name_prefix   = "${var.project}-gateway-lt-"
  image_id      = data.aws_ssm_parameter.al2023.value
  instance_type = var.instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.ec2.arn
  }
  vpc_security_group_ids = [var.gateway_sg_id]

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2 only
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    region         = var.region
    ssm_prefix     = var.ssm_prefix
    etcd_url_param = var.etcd_url_ssm_param
    role           = "gateway"
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.project}-gateway", role = "gateway" }
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_launch_template" "pubsub" {
  name_prefix   = "${var.project}-pubsub-lt-"
  image_id      = data.aws_ssm_parameter.al2023.value
  instance_type = var.instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.ec2.arn
  }
  vpc_security_group_ids = [var.pubsub_sg_id]

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required" # IMDSv2 only
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    region         = var.region
    ssm_prefix     = var.ssm_prefix
    etcd_url_param = var.etcd_url_ssm_param
    role           = "pubsub"
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.project}-pubsub", role = "pubsub" }
  }

  lifecycle { create_before_destroy = true }
}

# ---------- ASGs ----------
# gateway：掛 ALB target group、ELB health check。stateless → 可任意擴縮。
resource "aws_autoscaling_group" "gateway" {
  name_prefix               = "${var.project}-gateway-asg-"
  min_size                  = var.gateway_asg_min_size
  max_size                  = var.gateway_asg_max_size
  desired_capacity          = var.gateway_asg_desired_capacity
  vpc_zone_identifier       = var.private_subnet_ids
  target_group_arns         = [aws_lb_target_group.gateway.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 120

  launch_template {
    id      = aws_launch_template.gateway.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.project}-gateway"
    propagate_at_launch = true
  }
  # 供 CI/CD 用 SSM 依 tag 找實例（app=<project> + role=gateway|pubsub 分批部署）
  tag {
    key                 = "app"
    value               = var.project
    propagate_at_launch = true
  }
  tag {
    key                 = "role"
    value               = "gateway"
    propagate_at_launch = true
  }

  lifecycle { create_before_destroy = true }

  # app 參數要先存在，開機時的 deploy-app.sh 才讀得到（ETCD_URL 經 etcd_url_param 隱式依賴）
  depends_on = [aws_ssm_parameter.app]
}

# pubsub：不掛 ALB、EC2 health check。shard 歸屬由 etcd lease 決定，ASG 只負責「台數」：
# 縮容 / 換機時 SIGTERM → revoke lease → 其餘 pubsub 立刻接手；硬掛則等 LEASE_TTL_S 過期。
resource "aws_autoscaling_group" "pubsub" {
  name_prefix         = "${var.project}-pubsub-asg-"
  min_size            = var.pubsub_asg_min_size
  max_size            = var.pubsub_asg_max_size
  desired_capacity    = var.pubsub_asg_desired_capacity
  vpc_zone_identifier = var.private_subnet_ids
  health_check_type   = "EC2"

  launch_template {
    id      = aws_launch_template.pubsub.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.project}-pubsub"
    propagate_at_launch = true
  }
  tag {
    key                 = "app"
    value               = var.project
    propagate_at_launch = true
  }
  tag {
    key                 = "role"
    value               = "pubsub"
    propagate_at_launch = true
  }

  lifecycle { create_before_destroy = true }

  depends_on = [aws_ssm_parameter.app]
}
