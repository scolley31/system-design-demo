data "aws_caller_identity" "current" {}

# ---------- 打包 Lambda zip（純 Python: sqlalchemy + pg8000）----------
resource "null_resource" "build" {
  triggers = { src = filebase64sha256("${path.module}/src/cleanup.py") }
  provisioner "local-exec" {
    command = <<-EOT
      rm -rf "${path.module}/build" && mkdir -p "${path.module}/build"
      python3 -m pip install sqlalchemy==2.0.36 pg8000==1.31.2 --target "${path.module}/build" --quiet
      cp "${path.module}/src/cleanup.py" "${path.module}/build/"
    EOT
  }
}

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/build"
  output_path = "${path.module}/build.zip"
  depends_on  = [null_resource.build]
}

# ---------- Lambda SG（egress all）+ 允許進 RDS ----------
resource "aws_security_group" "lambda" {
  name_prefix = "${var.project}-cleanup-"
  description = "cleanup lambda"
  vpc_id      = var.vpc_id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  lifecycle { create_before_destroy = true }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_lambda" {
  security_group_id            = var.rds_sg_id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.lambda.id
  description                  = "cleanup lambda → RDS"
}

# ---------- Lambda IAM role ----------
resource "aws_iam_role" "lambda" {
  name_prefix = "${var.project}-cleanup-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "vpc" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# ---------- Lambda ----------
resource "aws_lambda_function" "cleanup" {
  function_name    = "${var.project}-cleanup"
  runtime          = "python3.11"
  handler          = "cleanup.lambda_handler"
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256
  role             = aws_iam_role.lambda.arn
  timeout          = 60
  memory_size      = 256

  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      DATABASE_URL           = var.database_url
      EXPIRED_GRACE_DAYS     = tostring(var.expired_grace_days)
      DELETED_RETENTION_DAYS = tostring(var.deleted_retention_days)
    }
  }
}

# ---------- EventBridge Scheduler（定時觸發）----------
resource "aws_iam_role" "scheduler" {
  name_prefix = "${var.project}-sched-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "${var.project}-sched-invoke"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = aws_lambda_function.cleanup.arn
    }]
  })
}

resource "aws_scheduler_schedule" "cleanup" {
  name                         = "${var.project}-cleanup"
  schedule_expression          = var.schedule
  schedule_expression_timezone = "UTC"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.cleanup.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}
