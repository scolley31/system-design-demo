output "cloudfront_domain" {
  value = aws_cloudfront_distribution.this.domain_name
}

output "cloudfront_url" {
  value = "https://${aws_cloudfront_distribution.this.domain_name}"
}

output "api_endpoint" {
  value = aws_apigatewayv2_api.this.api_endpoint
}

output "qr_bucket" {
  value = aws_s3_bucket.qr.bucket
}
