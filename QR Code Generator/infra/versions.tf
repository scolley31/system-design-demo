terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }

  # 正式版建議用 S3 + DynamoDB 遠端 state（先 bootstrap 一個 bucket/table 再取消註解）。
  # backend "s3" {
  #   bucket         = "qrcode-tfstate-<account-id>"
  #   key            = "prod/terraform.tfstate"
  #   region         = "ap-northeast-1"
  #   dynamodb_table = "qrcode-tflock"
  #   encrypt        = true
  # }
}
