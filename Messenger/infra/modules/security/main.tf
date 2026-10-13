# CloudFront → ALB 的來源 IP 範圍（AWS 代管 prefix list）。
# 第一層：只有 CloudFront edge 打得到 ALB；第二層：listener rule 驗 X-Origin-Verify header
# （prefix list 涵蓋「所有」CloudFront distribution，別人的 distribution 也在內，所以兩層都要）。
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# 對外 ALB（public subnets）：只接受 CloudFront origin-facing IP 的 80
resource "aws_security_group" "alb" {
  name_prefix = "${var.project}-alb-"
  description = "Internet-facing ALB (CloudFront origin only)"
  vpc_id      = var.vpc_id

  ingress {
    description     = "HTTP from CloudFront origin-facing"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
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

# gateway：只接受來自 ALB 的 8031（/ws、/api/*、/、/health）
resource "aws_security_group" "gateway" {
  name_prefix = "${var.project}-gateway-"
  description = "Gateway EC2 (client WebSocket entry)"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Gateway port from ALB"
    from_port       = 8031
    to_port         = 8031
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project}-gateway-sg" }

  lifecycle { create_before_destroy = true }
}

# pubsub：只接受來自 gateway 的 8041（/internal/ws 長連線 + /debug/*）
resource "aws_security_group" "pubsub" {
  name_prefix = "${var.project}-pubsub-"
  description = "Pub/Sub EC2 (chat shard owners)"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Internal WS/HTTP from gateway"
    from_port       = 8041
    to_port         = 8041
    protocol        = "tcp"
    security_groups = [aws_security_group.gateway.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project}-pubsub-sg" }

  lifecycle { create_before_destroy = true }
}

# etcd：client 2379 只給 gateway（watch shard map）與 pubsub（lease / claim）；2380 peer 只給自己
resource "aws_security_group" "etcd" {
  name_prefix = "${var.project}-etcd-"
  description = "etcd (service discovery + shard lease)"
  vpc_id      = var.vpc_id

  ingress {
    description     = "etcd client from gateway / pubsub"
    from_port       = 2379
    to_port         = 2379
    protocol        = "tcp"
    security_groups = [aws_security_group.gateway.id, aws_security_group.pubsub.id]
  }
  ingress {
    description = "etcd peer (Raft) between etcd members"
    from_port   = 2380
    to_port     = 2380
    protocol    = "tcp"
    self        = true
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.project}-etcd-sg" }

  lifecycle { create_before_destroy = true }
}
