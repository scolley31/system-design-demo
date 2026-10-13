output "cloudfront_domain" {
  value = aws_cloudfront_distribution.this.domain_name
}

output "cloudfront_url" {
  value = "https://${aws_cloudfront_distribution.this.domain_name}"
}

output "ws_url" {
  value = "wss://${aws_cloudfront_distribution.this.domain_name}/ws"
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.this.id
}

output "waf_web_acl_arn" {
  value = aws_wafv2_web_acl.cf.arn
}

output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "alb_arn_suffix" {
  value = aws_lb.this.arn_suffix
}
