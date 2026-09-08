data "aws_caller_identity" "current" {}

# Amazon Linux 2023 (arm64) 最新 AMI
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

# ---------- ECR ----------
# 單一 image：api / worker 兩種角色跑同一份映像，靠 ROLE env 區分（見下方兩組 ASG）。
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

# App 設定（開機時由 deploy-app.sh 撈進容器；對應 app/config.py）
resource "aws_ssm_parameter" "price_table" {
  name      = "${var.ssm_prefix}/PRICE_TABLE"
  type      = "String"
  value     = var.price_table_name
  overwrite = true
}

resource "aws_ssm_parameter" "amazon_domain" {
  name      = "${var.ssm_prefix}/AMAZON_DOMAIN"
  type      = "String"
  value     = var.amazon_domain
  overwrite = true
}

resource "aws_ssm_parameter" "crawl_rps" {
  name      = "${var.ssm_prefix}/CRAWL_RPS"
  type      = "String"
  value     = tostring(var.crawl_rps)
  overwrite = true
}

# 空字串 SSM 不收 → 以單一空白代替，deploy-app.sh 會 trim 回空字串（= email channel 停用）
resource "aws_ssm_parameter" "ses_from_email" {
  name      = "${var.ssm_prefix}/SES_FROM_EMAIL"
  type      = "String"
  value     = var.ses_from_email != "" ? var.ses_from_email : " "
  overwrite = true
}

resource "aws_ssm_parameter" "suspicious_drop_pct" {
  name      = "${var.ssm_prefix}/SUSPICIOUS_DROP_PCT"
  type      = "String"
  value     = tostring(var.suspicious_drop_pct)
  overwrite = true
}

resource "aws_ssm_parameter" "min_change_pct" {
  name      = "${var.ssm_prefix}/MIN_CHANGE_PCT"
  type      = "String"
  value     = tostring(var.min_change_pct)
  overwrite = true
}

# ---------- IAM (EC2 instance role) ----------
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

# 讀取 /<project>/* SSM 參數 + DB secret + 送 CloudWatch Logs
resource "aws_iam_role_policy" "app_config" {
  name = "${var.project}-app-config"
  role = aws_iam_role.ec2.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = "arn:aws:ssm:${var.region}:${data.aws_caller_identity.current.account_id}:parameter${var.ssm_prefix}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.db_secret_arn
      },
      {
        # Monitoring：容器用 awslogs driver 送 CloudWatch Logs
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/${var.project}/*",
          "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/${var.project}/*:*",
        ]
      }
    ]
  })
}

# price 表讀寫（DynamoPriceStore）+ DynamoDB Streams 讀取（DynamoStreamTailer，CDC）
resource "aws_iam_role_policy" "dynamo_price" {
  name = "${var.project}-dynamo-price"
  role = aws_iam_role.ec2.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:BatchWriteItem",
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:DescribeTable",
        ]
        Resource = [var.price_table_arn, "${var.price_table_arn}/index/*"]
      },
      {
        # cdc.py：describe_stream → get_shard_iterator → get_records
        Effect = "Allow"
        Action = [
          "dynamodb:DescribeStream",
          "dynamodb:GetShardIterator",
          "dynamodb:GetRecords",
          "dynamodb:ListStreams",
        ]
        Resource = "${var.price_table_arn}/stream/*"
      }
    ]
  })
}

# SES 寄信（只在 ses_from_email 有值時附加；email channel 見 app/channels.py）
resource "aws_iam_role_policy" "ses_send" {
  count = var.ses_from_email != "" ? 1 : 0
  name  = "${var.project}-ses-send"
  role  = aws_iam_role.ec2.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ses:SendEmail", "ses:SendRawEmail"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_instance_profile" "ec2" {
  name_prefix = "${var.project}-ec2-"
  role        = aws_iam_role.ec2.name
}

# ---------- Internal ALB（只服務 api ASG）----------
resource "aws_lb" "this" {
  name               = "${var.project}-alb"
  internal           = true
  load_balancer_type = "application"
  security_groups    = [var.alb_sg_id]
  subnets            = var.private_subnet_ids
}

resource "aws_lb_target_group" "app" {
  name        = "${var.project}-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "instance"

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

# ---------- Launch templates（api / worker：同 AMI、同映像，差在 ROLE）----------
# ROLE 由 user_data 注入容器。worker 角色跑 crawler + CDC tailer（DynamoDB Streams）+
# notify workers；api 角色只跑 web。app 端 ROLE env 已 plumbing（app/config.py），但目前
# 原型兩者跑同一份映像的完整程序（worker 在 API 程序內以 asyncio task 跑，見 app/main.py）；
# 日後 container entrypoint 可依 ROLE 分流成「只跑 web」/「只跑 worker fleet」。
resource "aws_launch_template" "api" {
  name_prefix   = "${var.project}-api-lt-"
  image_id      = data.aws_ssm_parameter.al2023.value
  instance_type = var.instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.ec2.arn
  }
  vpc_security_group_ids = [var.ec2_sg_id]

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    region     = var.region
    ssm_prefix = var.ssm_prefix
    role       = "api"
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.project}-api", role = "api" }
  }

  lifecycle { create_before_destroy = true }
}

resource "aws_launch_template" "worker" {
  name_prefix   = "${var.project}-worker-lt-"
  image_id      = data.aws_ssm_parameter.al2023.value
  instance_type = var.instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.ec2.arn
  }
  vpc_security_group_ids = [var.ec2_sg_id]

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    region     = var.region
    ssm_prefix = var.ssm_prefix
    role       = "worker"
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${var.project}-worker", role = "worker" }
  }

  lifecycle { create_before_destroy = true }
}

# ---------- ASGs ----------
# api：掛 ALB target group、ELB health check（API Gateway VPC Link → ALB 只打這組）。
resource "aws_autoscaling_group" "api" {
  name_prefix               = "${var.project}-api-asg-"
  min_size                  = var.asg_min_size
  max_size                  = var.asg_max_size
  desired_capacity          = var.asg_desired_capacity
  vpc_zone_identifier       = var.private_subnet_ids
  target_group_arns         = [aws_lb_target_group.app.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 120

  launch_template {
    id      = aws_launch_template.api.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.project}-api"
    propagate_at_launch = true
  }
  # 供 CI/CD 用 SSM 依 tag 找實例（api + worker 都帶 app=<project>，一起重新部署）
  tag {
    key                 = "app"
    value               = var.project
    propagate_at_launch = true
  }
  tag {
    key                 = "role"
    value               = "api"
    propagate_at_launch = true
  }

  lifecycle { create_before_destroy = true }
}

# worker：不掛 ALB、EC2 health check；跑 crawler（消費 crawl stream）+ CDC tailer + notify workers。
resource "aws_autoscaling_group" "worker" {
  name_prefix         = "${var.project}-worker-asg-"
  min_size            = var.worker_asg_min_size
  max_size            = var.worker_asg_max_size
  desired_capacity    = var.worker_asg_desired_capacity
  vpc_zone_identifier = var.private_subnet_ids
  health_check_type   = "EC2"

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.project}-worker"
    propagate_at_launch = true
  }
  tag {
    key                 = "app"
    value               = var.project
    propagate_at_launch = true
  }
  tag {
    key                 = "role"
    value               = "worker"
    propagate_at_launch = true
  }

  lifecycle { create_before_destroy = true }
}
