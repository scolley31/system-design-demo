output "alb_sg_id" {
  value = aws_security_group.alb.id
}

output "gateway_sg_id" {
  value = aws_security_group.gateway.id
}

output "pubsub_sg_id" {
  value = aws_security_group.pubsub.id
}

output "etcd_sg_id" {
  value = aws_security_group.etcd.id
}
