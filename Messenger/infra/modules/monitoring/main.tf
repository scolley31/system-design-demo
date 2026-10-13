terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1] # CloudFront 指標在 us-east-1
    }
  }
}

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
  actions       = [aws_sns_topic.alerts.arn]
  app_metric_ns = "${var.project}/app"
}

# ---------- App logs ----------
resource "aws_cloudwatch_log_group" "app" {
  name              = "/${var.project}/app"
  retention_in_days = 14
}

# ---------- Log metric filters（app 日誌 → 自訂指標）----------
# shard_takeover：pubsub 接手 shard（app/pubsub.py）。部署 / 縮容時會有一波，平時應為 0 → 只上 dashboard。
# id_conflict  ：同一 (chat_id, message_id) 被兩個 owner 寫入（fencing 失效的徵兆）→ 必告警。
# publish_failed：gateway 回 client 的 publish 失敗（UNAVAILABLE 等）。
#                 ⚠ 目前 app 只把它放在回給 client 的 frame，尚未寫進 log；app 補 log 後此 filter 才會有值。
locals {
  log_metrics = {
    ShardTakeover = "shard_takeover"
    IdConflict    = "id_conflict"
    PublishFailed = "publish_failed"
  }
}

resource "aws_cloudwatch_log_metric_filter" "app" {
  for_each       = local.log_metrics
  name           = "${var.project}-${each.value}"
  log_group_name = aws_cloudwatch_log_group.app.name
  pattern        = "\"${each.value}\""

  metric_transformation {
    name          = each.key
    namespace     = local.app_metric_ns
    value         = "1"
    default_value = "0"
  }
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

# ALB 自己產生的 5xx（502/503/504：gateway 全掛 / 無健康 target / 握手逾時）
resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${var.project}-alb-elb-5xx"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 5
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_ELB_5XX_Count"
  period              = 300
  statistic           = "Sum"
  dimensions          = { LoadBalancer = var.alb_arn_suffix }
  alarm_actions       = local.actions
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "asg_cpu" {
  for_each = {
    gateway = var.gateway_asg_name
    pubsub  = var.pubsub_asg_name
  }
  alarm_name          = "${var.project}-${each.key}-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 80
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  period              = 300
  statistic           = "Average"
  dimensions          = { AutoScalingGroupName = each.value }
  alarm_actions       = local.actions
}

# etcd 是 demo SPOF：instance / system status check 失敗 → 全部 shard 會在 LEASE_TTL_S 後失去 owner
resource "aws_cloudwatch_metric_alarm" "etcd_status" {
  alarm_name          = "${var.project}-etcd-status-check"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  threshold           = 1
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  period              = 60
  statistic           = "Maximum"
  dimensions          = { InstanceId = var.etcd_instance_id }
  alarm_actions       = local.actions
  ok_actions          = local.actions
}

# DynamoDB message：寫入熱點（單一超熱群聊 = 單一 partition key）時可能 throttle
resource "aws_cloudwatch_metric_alarm" "dynamo_throttle" {
  for_each = {
    write = "WriteThrottleEvents"
    read  = "ReadThrottleEvents"
  }
  alarm_name          = "${var.project}-dynamo-message-${each.key}-throttle"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  namespace           = "AWS/DynamoDB"
  metric_name         = each.value
  period              = 300
  statistic           = "Sum"
  dimensions          = { TableName = var.message_table_name }
  alarm_actions       = local.actions
  treat_missing_data  = "notBreaching"
}

# id_conflict 出現一次就要查（per-chat 序號被兩個 owner 指派 = 單一寫入者保證被破壞）
resource "aws_cloudwatch_metric_alarm" "id_conflict" {
  alarm_name          = "${var.project}-id-conflict"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  namespace           = local.app_metric_ns
  metric_name         = "IdConflict"
  period              = 300
  statistic           = "Sum"
  alarm_actions       = local.actions
  treat_missing_data  = "notBreaching"
}

resource "aws_cloudwatch_metric_alarm" "publish_failed" {
  alarm_name          = "${var.project}-publish-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 10
  namespace           = local.app_metric_ns
  metric_name         = "PublishFailed"
  period              = 300
  statistic           = "Sum"
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
          title  = "ALB 連線 / 5xx / healthy gateways",
          region = var.region,
          metrics = [
            ["AWS/ApplicationELB", "ActiveConnectionCount", "LoadBalancer", var.alb_arn_suffix, { stat = "Sum" }],
            [".", "HTTPCode_ELB_5XX_Count", ".", ".", { stat = "Sum" }],
            [".", "HealthyHostCount", "TargetGroup", var.tg_arn_suffix, "LoadBalancer", var.alb_arn_suffix],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6,
        properties = {
          title  = "App：shard_takeover / id_conflict / publish_failed",
          region = var.region,
          stat   = "Sum",
          metrics = [
            [local.app_metric_ns, "ShardTakeover"],
            [".", "IdConflict"],
            [".", "PublishFailed"],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6,
        properties = {
          title  = "EC2 CPU（gateway / pubsub）",
          region = var.region,
          metrics = [
            ["AWS/EC2", "CPUUtilization", "AutoScalingGroupName", var.gateway_asg_name],
            ["...", var.pubsub_asg_name],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6,
        properties = {
          title  = "DynamoDB message 寫入 / throttle",
          region = var.region,
          stat   = "Sum",
          metrics = [
            ["AWS/DynamoDB", "ConsumedWriteCapacityUnits", "TableName", var.message_table_name],
            [".", "WriteThrottleEvents", ".", "."],
            [".", "ReadThrottleEvents", ".", "."],
          ]
        }
      },
    ]
  })
}
