data "aws_caller_identity" "current" {}

# ---------- API Gateway HTTP API → VPC Link → 內部 ALB ----------
resource "aws_security_group" "vpclink" {
  name_prefix = "${var.project}-vpclink-"
  description = "API Gateway VPC Link"
  vpc_id      = var.vpc_id
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  lifecycle { create_before_destroy = true }
}

resource "aws_apigatewayv2_vpc_link" "this" {
  name               = "${var.project}-vpclink"
  subnet_ids         = var.private_subnet_ids
  security_group_ids = [aws_security_group.vpclink.id]
}

resource "aws_apigatewayv2_api" "this" {
  name          = "${var.project}-http-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "alb" {
  api_id             = aws_apigatewayv2_api.this.id
  integration_type   = "HTTP_PROXY"
  integration_method = "ANY"
  integration_uri    = var.alb_listener_arn
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.this.id
}

resource "aws_apigatewayv2_route" "proxy" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_route" "root" {
  api_id    = aws_apigatewayv2_api.this.id
  route_key = "ANY /"
  target    = "integrations/${aws_apigatewayv2_integration.alb.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true
}

# ---------- S3 (QR 圖片) ----------
resource "aws_s3_bucket" "qr" {
  bucket_prefix = "${var.project}-qr-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "qr" {
  bucket                  = aws_s3_bucket.qr.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "s3" {
  name                              = "${var.project}-s3-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ---------- CloudFront ----------
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}
data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}
data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

locals {
  api_host = replace(aws_apigatewayv2_api.this.api_endpoint, "https://", "")
}

resource "aws_cloudfront_distribution" "this" {
  enabled         = true
  comment         = "${var.project} front door"
  is_ipv6_enabled = true

  # Origin 1: API Gateway (動態：/, /r/*, /api/*)
  origin {
    origin_id   = "apigw"
    domain_name = local.api_host
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # Origin 2: S3 (靜態 QR 圖片：/qr-img/*)
  origin {
    origin_id                = "s3"
    domain_name              = aws_s3_bucket.qr.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.s3.id
  }

  # 預設 → API Gateway，關快取（redirect/api 每次回源，第 6 題）
  default_cache_behavior {
    target_origin_id         = "apigw"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  # /qr-img/* → S3，長快取
  ordered_cache_behavior {
    path_pattern           = "/qr-img/*"
    target_origin_id       = "s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# S3 bucket policy：只允許此 CloudFront distribution（OAC）讀取
resource "aws_s3_bucket_policy" "qr" {
  bucket = aws_s3_bucket.qr.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontOAC"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.qr.arn}/*"
      Condition = {
        StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.this.arn }
      }
    }]
  })
}

# 授予 EC2 instance role 對 QR bucket 寫入（create 時上傳 PNG）
resource "aws_iam_role_policy" "ec2_s3_put" {
  name = "${var.project}-ec2-s3-put"
  role = var.ec2_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject"]
      Resource = "${aws_s3_bucket.qr.arn}/*"
    }]
  })
}

# ---------- 對外設定寫入 SSM（EC2 開機讀取注入容器）----------
resource "aws_ssm_parameter" "base_url" {
  name      = "${var.ssm_prefix}/BASE_URL"
  type      = "String"
  value     = "https://${aws_cloudfront_distribution.this.domain_name}"
  overwrite = true
}

resource "aws_ssm_parameter" "s3_bucket" {
  name      = "${var.ssm_prefix}/S3_BUCKET"
  type      = "String"
  value     = aws_s3_bucket.qr.bucket
  overwrite = true
}

resource "aws_ssm_parameter" "cdn_base" {
  name      = "${var.ssm_prefix}/CDN_BASE"
  type      = "String"
  value     = "https://${aws_cloudfront_distribution.this.domain_name}"
  overwrite = true
}
