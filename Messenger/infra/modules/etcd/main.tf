# ---------- etcd（service discovery + shard lease）----------
# pubsub 節點在這裡 grant lease → claim shards/{i}（txn create-if-absent）→ keepalive；
# gateway 只 range + watch nodes/ 與 shards/，同步出 chat → owner 的路由表（app/registry.py）。
#
# ⚠ demo 單點（SPOF）：只有 1 台 etcd。它掛掉 → pubsub 無法 keepalive → 所有 shard lease 過期 →
#   publish 一律 UNAVAILABLE，直到 etcd 回來（資料在 root gp3，重開機 / 重建容器不丟 key）。
#   production = 3 台跨 3 AZ（node_count=3，每台固定 IP，--initial-cluster 三台靜態成員，
#   Raft 容忍 1 台故障）；或改用 DynamoDB lock table 做 lease，免維運 etcd，但 registry 要重寫。
#
# 不放 ASG：etcd 成員身分綁固定 IP + data dir，ASG 換機會變成「新成員」。
# 也不帶 app=messenger tag → CI/CD 的 SSM run-command 永遠打不到這台（tag app=messenger-etcd）。

# Amazon Linux 2023 (arm64) 最新 AMI
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  # 固定私有 IP（子網第 50 個位址）：etcd 的 advertise URL / ETCD_URL 都寫死這個位址
  private_ip = cidrhost(var.subnet_cidr, 50)
  etcd_url   = "http://${local.private_ip}:2379"
}

# ---------- IAM（只要 SSM Session Manager，方便 etcdctl 維運；不需其他權限）----------
resource "aws_iam_role" "etcd" {
  name_prefix = "${var.project}-etcd-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.etcd.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "etcd" {
  name_prefix = "${var.project}-etcd-"
  role        = aws_iam_role.etcd.name
}

resource "aws_instance" "etcd" {
  count                  = var.node_count
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  private_ip             = local.private_ip
  vpc_security_group_ids = [var.etcd_sg_id]
  iam_instance_profile   = aws_iam_instance_profile.etcd.name

  # IMDSv2 only
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    dnf install -y docker
    systemctl enable --now docker
    mkdir -p /var/lib/etcd
    docker rm -f etcd 2>/dev/null || true
    # data dir 掛在 root gp3（/var/lib/etcd）；restart always → 開機 / crash 自動拉起
    docker run -d --name etcd --restart always --network host \
      -v /var/lib/etcd:/etcd-data \
      quay.io/coreos/etcd:v3.5.17 \
      etcd \
      --name=${var.project}-etcd-0 \
      --data-dir=/etcd-data \
      --listen-client-urls=http://0.0.0.0:2379 \
      --advertise-client-urls=${local.etcd_url} \
      --listen-peer-urls=http://0.0.0.0:2380 \
      --initial-advertise-peer-urls=http://${local.private_ip}:2380 \
      --initial-cluster=${var.project}-etcd-0=http://${local.private_ip}:2380
  EOT

  # AMI 更新不要觸發重建（重建 = 換新 data dir = shard map 清空，所有 pubsub 重新 claim）
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  tags = { Name = "${var.project}-etcd-${count.index}", app = "${var.project}-etcd" }
}

# gateway / pubsub 開機時由 deploy-app.sh 讀取注入容器 ETCD_URL
resource "aws_ssm_parameter" "etcd_url" {
  name      = "${var.ssm_prefix}/ETCD_URL"
  type      = "String"
  value     = local.etcd_url
  overwrite = true
}
