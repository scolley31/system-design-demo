terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1] # CLOUDFRONT scope 的 WAF 必須在 us-east-1
    }
  }
}

# ---------- 為什麼沒有 API Gateway ----------
# - HTTP API（sibling price 用的 VPC Link 架構）不能代理 WebSocket Upgrade → /ws 根本連不上。
# - WebSocket API 也不採用：
#   1) 計價是「每則訊息 + 連線分鐘」，聊天訊息量大時遠貴於 ALB；
#   2) 單一連線最長 2 小時就被切斷，client 得定期重連；
#   3) 推播要對每個連線各打一次 @connections POST → 一則群訊 = N 次 RPC，
#      正是 PDF 否決的 fan-out 方式（我們的 pubsub 一次 publish、gateway 端本地 fan-out）。
# → 改為 CloudFront（原生支援 WebSocket）→ 對外 ALB → gateway ASG。

# ---------- Origin verify secret ----------
# ALB 在 public subnet，SG 只放 CloudFront origin-facing prefix list；但該 prefix list 涵蓋
# 「所有人」的 CloudFront，所以再加一道 header 密鑰：只有我們的 distribution 會帶。
resource "random_password" "origin_verify" {
  length  = 32
  special = false
}

# ---------- 對外 ALB（public subnets，只服務 gateway ASG）----------
# idle_timeout 300：WebSocket 長連線（app 端 ws-ping 每 20s，遠低於此值）。
# production：ALB 掛 ACM 憑證 + CloudFront origin 改 https-only（目前 CloudFront → ALB 是明文 HTTP）；
# 或改用 CloudFront VPC origin 直連 internal ALB（ALB 不需要 public IP，也不需要 header 密鑰）。
resource "aws_lb" "this" {
  name                       = "${var.project}-alb"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [var.alb_sg_id]
  subnets                    = var.public_subnet_ids
  idle_timeout               = 300
  drop_invalid_header_fields = true
}

# 預設一律 403：沒帶正確 X-Origin-Verify（= 繞過 CloudFront / WAF 直打 ALB）
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }
}

resource "aws_lb_listener_rule" "from_cloudfront" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 1

  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [random_password.origin_verify.result]
    }
  }

  action {
    type             = "forward"
    target_group_arn = var.gateway_tg_arn
  }
}

# ---------- CloudFront ----------
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

# ---------- WAF（per-IP rate limiting，掛 CloudFront）----------
# scope=CLOUDFRONT 的 web ACL 必須建在 us-east-1。
# WebSocket 只有握手那個 GET /ws 會被計數，連線內的 frame 不經 WAF 規則。
resource "aws_wafv2_web_acl" "cf" {
  provider    = aws.us_east_1
  name        = "${var.project}-cf-waf"
  description = "${var.project} per-IP rate limiting"
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  # Rule 1：/api/* HTTP 端點，較嚴的 per-IP 上限
  rule {
    name     = "api-rate"
    priority = 1
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_api
        aggregate_key_type = "IP"
        scope_down_statement {
          byte_match_statement {
            positional_constraint = "STARTS_WITH"
            search_string         = "/api/"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project}-api-rate"
      sampled_requests_enabled   = true
    }
  }

  # Rule 2：全域 per-IP 上限（含 /ws 握手 → 擋重連風暴）
  rule {
    name     = "global-rate"
    priority = 2
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit_global
        aggregate_key_type = "IP"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project}-global-rate"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project}-cf-waf"
    sampled_requests_enabled   = true
  }
}

resource "aws_cloudfront_distribution" "this" {
  enabled         = true
  comment         = "${var.project} front door (HTTP + WebSocket)"
  is_ipv6_enabled = true
  web_acl_id      = aws_wafv2_web_acl.cf.arn

  # 單一 Origin：對外 ALB（http-only；production 換 ACM + https-only，見上方 ALB 註解）
  origin {
    origin_id   = "alb"
    domain_name = aws_lb.this.dns_name
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_verify.result
    }
  }

  # 全部 → ALB，關快取。CloudFront 原生支援 WebSocket：只要 viewer 帶 Upgrade header 就會轉給 origin，
  # AllViewerExceptHostHeader 會把 Sec-WebSocket-* 與 query string（?token= / ?user_id=）原樣帶過去。
  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
