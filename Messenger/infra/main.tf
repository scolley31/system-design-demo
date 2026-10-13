locals {
  ssm_prefix = "/${var.project}"
}

module "network" {
  source               = "./modules/network"
  project              = var.project
  region               = var.region
  vpc_cidr             = var.vpc_cidr
  azs                  = var.azs
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
  single_nat_gateway   = var.single_nat_gateway
}

module "security" {
  source  = "./modules/security"
  project = var.project
  vpc_id  = module.network.vpc_id
}

module "data" {
  source  = "./modules/data"
  project = var.project
}

module "etcd" {
  source        = "./modules/etcd"
  project       = var.project
  region        = var.region
  subnet_id     = module.network.private_subnet_ids[0]
  subnet_cidr   = var.private_subnet_cidrs[0]
  etcd_sg_id    = module.security.etcd_sg_id
  instance_type = var.etcd_instance_type
  node_count    = var.etcd_node_count
  ssm_prefix    = local.ssm_prefix
}

module "compute" {
  source                       = "./modules/compute"
  project                      = var.project
  region                       = var.region
  vpc_id                       = module.network.vpc_id
  private_subnet_ids           = module.network.private_subnet_ids
  gateway_sg_id                = module.security.gateway_sg_id
  pubsub_sg_id                 = module.security.pubsub_sg_id
  instance_type                = var.ec2_instance_type
  gateway_asg_min_size         = var.gateway_asg_min_size
  gateway_asg_max_size         = var.gateway_asg_max_size
  gateway_asg_desired_capacity = var.gateway_asg_desired_capacity
  pubsub_asg_min_size          = var.pubsub_asg_min_size
  pubsub_asg_max_size          = var.pubsub_asg_max_size
  pubsub_asg_desired_capacity  = var.pubsub_asg_desired_capacity
  ssm_prefix                   = local.ssm_prefix
  etcd_url_ssm_param           = module.etcd.etcd_url_ssm_param
  table_prefix                 = module.data.table_prefix
  table_arns                   = module.data.table_arns
  num_shards                   = var.num_shards
  lease_ttl_s                  = var.lease_ttl_s
  max_participants             = var.max_participants
  allow_debug                  = var.allow_debug
  auth_enabled                 = var.auth_enabled
}

module "edge" {
  source = "./modules/edge"
  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }
  project               = var.project
  public_subnet_ids     = module.network.public_subnet_ids
  alb_sg_id             = module.security.alb_sg_id
  gateway_tg_arn        = module.compute.gateway_tg_arn
  waf_rate_limit_api    = var.waf_rate_limit_api
  waf_rate_limit_global = var.waf_rate_limit_global
}

module "auth" {
  source         = "./modules/auth"
  project        = var.project
  region         = var.region
  ssm_prefix     = local.ssm_prefix
  cloudfront_url = module.edge.cloudfront_url
}

module "monitoring" {
  source = "./modules/monitoring"
  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }
  project                    = var.project
  region                     = var.region
  alert_email                = var.alert_email
  alb_arn_suffix             = module.edge.alb_arn_suffix
  tg_arn_suffix              = module.compute.gateway_tg_arn_suffix
  gateway_asg_name           = module.compute.gateway_asg_name
  pubsub_asg_name            = module.compute.pubsub_asg_name
  etcd_instance_id           = module.etcd.instance_id
  message_table_name         = module.data.table_names["message"]
  cloudfront_distribution_id = module.edge.cloudfront_distribution_id
}

module "cicd" {
  source             = "./modules/cicd"
  project            = var.project
  region             = var.region
  github_repo        = var.github_repo
  ssm_prefix         = local.ssm_prefix
  ecr_repository_arn = module.compute.ecr_repository_arn
}
