terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1] # CloudFront 指標在 us-east-1
    }
  }
}

data "aws_caller_identity" "current" {}

# ---------- SNS（email 通知）----------
resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email # ⚠ 建立後需收信點確認才生效
}

locals {
  actions = [aws_sns_topic.alerts.arn]
}

# ---------- App logs ----------
resource "aws_cloudwatch_log_group" "app" {
  name              = "/${var.project}/app"
  retention_in_days = 14
}

# ---------- Alarms（→ SNS）----------
resource "aws_cloudwatch_metric_alarm" "alb_unhealthy" {
  alarm_name          = "${var.project}-alb-unhealthy-hosts"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  threshold           = 1
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  period              = 60
  statistic           = "Maximum"
  dimensions          = { LoadBalancer = var.alb_arn_suffix, TargetGroup = var.tg_arn_suffix }
  alarm_actions       = local.actions
  ok_actions          = local.actions
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${var.project}-alb-target-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 5
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  period              = 300
  statistic           = "Sum"
  dimensions          = { LoadBalancer = var.alb_arn_suffix }
  alarm_actions       = local.actions
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "alb_latency" {
  alarm_name          = "${var.project}-alb-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 1
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  period              = 60
  statistic           = "Average"
  dimensions          = { LoadBalancer = var.alb_arn_suffix }
  alarm_actions       = local.actions
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "ec2_cpu" {
  alarm_name          = "${var.project}-ec2-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 80
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  period              = 300
  statistic           = "Average"
  dimensions          = { AutoScalingGroupName = var.asg_name }
  alarm_actions       = local.actions
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${var.project}-rds-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 80
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  period              = 300
  statistic           = "Average"
  dimensions          = { DBInstanceIdentifier = var.db_instance_id }
  alarm_actions       = local.actions
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "${var.project}-rds-low-storage"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  threshold           = 2147483648 # 2 GB
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  period              = 300
  statistic           = "Average"
  dimensions          = { DBInstanceIdentifier = var.db_instance_id }
  alarm_actions       = local.actions
}

resource "aws_cloudwatch_metric_alarm" "redis_cpu" {
  alarm_name          = "${var.project}-redis-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 80
  namespace           = "AWS/ElastiCache"
  metric_name         = "CPUUtilization"
  period              = 300
  statistic           = "Average"
  dimensions          = { CacheClusterId = var.redis_cluster_id }
  alarm_actions       = local.actions
}

resource "aws_cloudwatch_metric_alarm" "apigw_5xx" {
  alarm_name          = "${var.project}-apigw-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 5
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  period              = 300
  statistic           = "Sum"
  dimensions          = { ApiId = var.api_id }
  alarm_actions       = local.actions
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.project}-cleanup-lambda-errors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  threshold           = 1
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  period              = 300
  statistic           = "Sum"
  dimensions          = { FunctionName = var.lambda_name }
  alarm_actions       = local.actions
  treat_missing_data  = "notBreaching"
}

# CloudFront 指標在 us-east-1
resource "aws_cloudwatch_metric_alarm" "cloudfront_5xx" {
  provider            = aws.us_east_1
  alarm_name          = "${var.project}-cloudfront-5xx-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 5
  namespace           = "AWS/CloudFront"
  metric_name         = "5xxErrorRate"
  period              = 300
  statistic           = "Average"
  dimensions          = { DistributionId = var.cloudfront_distribution_id, Region = "Global" }
  alarm_actions       = local.actions
  treat_missing_data  = "notBreaching"
}

# ---------- Dashboard ----------
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${var.project}-overview"
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6,
        properties = {
          title  = "ALB 5xx / latency / healthy",
          region = var.region,
          metrics = [
            ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", var.alb_arn_suffix],
            [".", "TargetResponseTime", ".", ".", { stat = "Average" }],
            [".", "HealthyHostCount", "TargetGroup", var.tg_arn_suffix, "LoadBalancer", var.alb_arn_suffix],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "RDS CPU / storage / connections",
          region = var.region,
          metrics = [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.db_instance_id],
            [".", "FreeStorageSpace", ".", "."],
            [".", "DatabaseConnections", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6,
        properties = {
          title  = "API Gateway count / 5xx / latency",
          region = var.region,
          metrics = [
            ["AWS/ApiGateway", "Count", "ApiId", var.api_id],
            [".", "5xx", ".", "."],
            [".", "Latency", ".", ".", { stat = "Average" }],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6,
        properties = {
          title  = "EC2 CPU / cleanup Lambda errors",
          region = var.region,
          metrics = [
            ["AWS/EC2", "CPUUtilization", "AutoScalingGroupName", var.asg_name],
            ["AWS/Lambda", "Errors", "FunctionName", var.lambda_name],
          ]
        }
      },
    ]
  })
}

# ---------- Synthetic canary（端到端 /health）----------
resource "aws_s3_bucket" "canary" {
  bucket_prefix = "${var.project}-canary-"
  force_destroy = true
}

resource "aws_iam_role" "canary" {
  name_prefix = "${var.project}-canary-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "canary" {
  name = "${var.project}-canary"
  role = aws_iam_role.canary.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetBucketLocation"]
        Resource = ["${aws_s3_bucket.canary.arn}", "${aws_s3_bucket.canary.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/cwsyn-*"
      },
      {
        Effect    = "Allow"
        Action    = ["cloudwatch:PutMetricData"]
        Resource  = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = "CloudWatchSynthetics" } }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListAllMyBuckets"]
        Resource = "*"
      }
    ]
  })
}

data "archive_file" "canary" {
  type        = "zip"
  source_dir  = "${path.module}/canary"
  output_path = "${path.module}/canary.zip"
}

resource "aws_synthetics_canary" "health" {
  name                 = "${var.project}-health"
  artifact_s3_location = "s3://${aws_s3_bucket.canary.id}/canary"
  execution_role_arn   = aws_iam_role.canary.arn
  runtime_version      = "syn-nodejs-puppeteer-9.1"
  handler              = "heartbeat.handler"
  zip_file             = data.archive_file.canary.output_path
  start_canary         = true
  delete_lambda        = true

  schedule {
    expression = "rate(5 minutes)"
  }
  run_config {
    timeout_in_seconds = 60
    environment_variables = {
      TARGET_URL = "${var.cloudfront_url}/health"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "canary" {
  alarm_name          = "${var.project}-canary-health"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  threshold           = 90
  namespace           = "CloudWatchSynthetics"
  metric_name         = "SuccessPercent"
  period              = 300
  statistic           = "Average"
  dimensions          = { CanaryName = aws_synthetics_canary.health.name }
  alarm_actions       = local.actions
  treat_missing_data  = "breaching"
}
