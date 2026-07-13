# 內部 ALB：接受來自 VPC 內（API Gateway VPC Link ENI 在私有子網）的流量
resource "aws_security_group" "alb" {
  name_prefix = "${var.project}-alb-"
  description = "Internal ALB"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTP from within VPC (API Gateway VPC Link)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project}-alb-sg" }

  lifecycle { create_before_destroy = true }
}

# EC2：只接受來自 ALB 的 8000
resource "aws_security_group" "ec2" {
  name_prefix = "${var.project}-ec2-"
  description = "App EC2 instances"
  vpc_id      = var.vpc_id

  ingress {
    description     = "App port from ALB"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project}-ec2-sg" }

  lifecycle { create_before_destroy = true }
}

# RDS：只接受來自 EC2 的 5432
resource "aws_security_group" "rds" {
  name_prefix = "${var.project}-rds-"
  description = "RDS PostgreSQL"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Postgres from EC2"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ec2.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project}-rds-sg" }

  lifecycle { create_before_destroy = true }
}

# Redis：只接受來自 EC2 的 6379
resource "aws_security_group" "redis" {
  name_prefix = "${var.project}-redis-"
  description = "ElastiCache Redis"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Redis from EC2"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ec2.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project}-redis-sg" }

  lifecycle { create_before_destroy = true }
}
