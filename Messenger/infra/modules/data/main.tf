# ---------- DynamoDB（5 張表，PAY_PER_REQUEST）----------
# 表名 = "${project}-${name}" → app 的 DYNAMO_TABLE_PREFIX = "${project}-"（見 app/store.py TABLES）。
# key schema 必須與 app/store.py TABLES 一致；app 沒有 CreateTable 權限，表一律由這裡建。
#
#   chat        chat_id                         群組 metadata（commit marker：最後寫入 = 建群成功）
#   membership  chat_id + user_id               成員列表（fan-out 對象）
#   message     chat_id + message_id(N)         訊息本體；message_id = per-chat 連續序號（owner 指派）
#   chat_state  user_id + chat_id               每人每 chat 的已讀/已送達位置（重連補收的起點）
#   client_msg  chat_id + client_msg_id         client 端重送去重；TTL expires_at 自動清（DEDUPE_TTL_S）
#
# 訊息永久保留（不設 TTL、不跑清理排程）；唯一會過期的是 client_msg 去重紀錄（DynamoDB 原生 TTL）。
locals {
  tables = {
    chat       = { hash_key = "chat_id", hash_type = "S", range_key = null, range_type = null }
    membership = { hash_key = "chat_id", hash_type = "S", range_key = "user_id", range_type = "S" }
    message    = { hash_key = "chat_id", hash_type = "S", range_key = "message_id", range_type = "N" }
    chat_state = { hash_key = "user_id", hash_type = "S", range_key = "chat_id", range_type = "S" }
    client_msg = { hash_key = "chat_id", hash_type = "S", range_key = "client_msg_id", range_type = "S" }
  }
}

resource "aws_dynamodb_table" "this" {
  for_each     = local.tables
  name         = "${var.project}-${each.key}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = each.value.hash_key
  range_key    = each.value.range_key

  attribute {
    name = each.value.hash_key
    type = each.value.hash_type
  }

  dynamic "attribute" {
    for_each = each.value.range_key == null ? [] : [each.value.range_key]
    content {
      name = attribute.value
      type = each.value.range_type
    }
  }

  # client_msg：去重紀錄過了 DEDUPE_TTL_S 就沒用了 → 原生 TTL 清除（免費、非即時，約 48h 內）
  dynamic "ttl" {
    for_each = each.key == "client_msg" ? [1] : []
    content {
      attribute_name = "expires_at"
      enabled        = true
    }
  }

  # message 是唯一不可重建的資料 → 開 PITR（35 天內任意時間點還原）
  point_in_time_recovery {
    enabled = each.key == "message"
  }
}
