locals {
  issuer        = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.this.id}"
  hosted_domain = "${aws_cognito_user_pool_domain.this.domain}.auth.${var.region}.amazoncognito.com"
}

# ---------- Cognito User Pool ----------
# 沒有 API Gateway JWT authorizer：WebSocket 握手無法帶自訂 Authorization header（瀏覽器 API 限制），
# client 改以 ?token=<id token> 連 /ws，由 gateway 自己驗 iss/aud/exp/簽章（app/auth.py）。
# 開關在 compute 模組的 AUTH_ENABLED（var.auth_enabled）。
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

# ---------- SSM（gateway 容器驗 JWT 用；見 app/auth.py）----------
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
