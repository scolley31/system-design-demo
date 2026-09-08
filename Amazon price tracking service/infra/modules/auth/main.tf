locals {
  issuer        = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.this.id}"
  hosted_domain = "${aws_cognito_user_pool_domain.this.domain}.auth.${var.region}.amazoncognito.com"
}

# ---------- Cognito User Pool ----------
resource "aws_cognito_user_pool" "this" {
  name                     = "${var.project}-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_numbers   = true
    require_uppercase = true
    require_symbols   = false
  }
}

# 公開 SPA client（無 secret，OAuth2 authorization code + PKCE）
resource "aws_cognito_user_pool_client" "spa" {
  name         = "${var.project}-spa"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = [var.cloudfront_url]
  logout_urls                          = [var.cloudfront_url]
  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
}

# Hosted UI domain（prefix 須全域唯一）
resource "random_string" "domain" {
  length  = 6
  special = false
  upper   = false
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = "${var.project}-${random_string.domain.result}"
  user_pool_id = aws_cognito_user_pool.this.id
}

# ---------- API Gateway JWT authorizer + 受保護 routes ----------
resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = var.api_id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${var.project}-jwt"
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.spa.id]
    issuer   = local.issuer
  }
}

locals {
  # 受保護的使用者端點（比 edge 的 "ANY /{proxy+}" 更精確 → 優先匹配並套用 authorizer）。
  # 注意：GET /api/v1/stream（SSE）等公開端點不在此 → 落到 proxy 維持公開
  # （SSE 由 user_id 帶識別；正式版走 push / email）。
  # POST /api/v1/price-reports 是 extension 匿名眾包回報（reporter_token 限流），維持公開；
  # /api/v1/config/meta、/api/v1/products、/api/v1/price/{id} 亦維持公開。
  protected_routes = [
    "POST /api/v1/subscriptions",
    "GET /api/v1/subscriptions",
    "DELETE /api/v1/subscriptions/{subscription_id}",
    "GET /api/v1/notifications",
  ]
}

resource "aws_apigatewayv2_route" "protected" {
  for_each           = toset(local.protected_routes)
  api_id             = var.api_id
  route_key          = each.value
  target             = "integrations/${var.integration_id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

# ---------- SSM（EC2 容器與前端設定）----------
resource "aws_ssm_parameter" "auth_enabled" {
  name      = "${var.ssm_prefix}/AUTH_ENABLED"
  type      = "String"
  value     = "true"
  overwrite = true
}
resource "aws_ssm_parameter" "cognito_region" {
  name      = "${var.ssm_prefix}/COGNITO_REGION"
  type      = "String"
  value     = var.region
  overwrite = true
}
resource "aws_ssm_parameter" "cognito_pool" {
  name      = "${var.ssm_prefix}/COGNITO_USER_POOL_ID"
  type      = "String"
  value     = aws_cognito_user_pool.this.id
  overwrite = true
}
resource "aws_ssm_parameter" "cognito_client" {
  name      = "${var.ssm_prefix}/COGNITO_CLIENT_ID"
  type      = "String"
  value     = aws_cognito_user_pool_client.spa.id
  overwrite = true
}
resource "aws_ssm_parameter" "cognito_domain" {
  name      = "${var.ssm_prefix}/COGNITO_DOMAIN"
  type      = "String"
  value     = local.hosted_domain
  overwrite = true
}
resource "aws_ssm_parameter" "cognito_redirect" {
  name      = "${var.ssm_prefix}/COGNITO_REDIRECT_URI"
  type      = "String"
  value     = var.cloudfront_url
  overwrite = true
}
