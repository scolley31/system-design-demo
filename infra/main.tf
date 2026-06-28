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
  source   = "./modules/security"
  project  = var.project
  vpc_id   = module.network.vpc_id
  vpc_cidr = module.network.vpc_cidr
}

module "data" {
  source             = "./modules/data"
  project            = var.project
  private_subnet_ids = module.network.private_subnet_ids
  rds_sg_id          = module.security.rds_sg_id
  redis_sg_id        = module.security.redis_sg_id

  db_engine_version    = var.db_engine_version
  db_instance_class    = var.db_instance_class
  db_allocated_storage = var.db_allocated_storage
  db_multi_az          = var.db_multi_az
  db_name              = var.db_name
  db_username          = var.db_username

  redis_node_type      = var.redis_node_type
  redis_engine_version = var.redis_engine_version
}

module "compute" {
  source               = "./modules/compute"
  project              = var.project
  region               = var.region
  vpc_id               = module.network.vpc_id
  private_subnet_ids   = module.network.private_subnet_ids
  ec2_sg_id            = module.security.ec2_sg_id
  alb_sg_id            = module.security.alb_sg_id
  instance_type        = var.ec2_instance_type
  asg_min_size         = var.asg_min_size
  asg_max_size         = var.asg_max_size
  asg_desired_capacity = var.asg_desired_capacity
  ssm_prefix           = local.ssm_prefix
  db_secret_arn        = module.data.db_secret_arn
}

module "edge" {
  source = "./modules/edge"
  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }
  project               = var.project
  region                = var.region
  vpc_id                = module.network.vpc_id
  private_subnet_ids    = module.network.private_subnet_ids
  alb_listener_arn      = module.compute.alb_listener_arn
  ssm_prefix            = local.ssm_prefix
  ec2_role_name         = module.compute.ec2_role_name
  waf_rate_limit_api    = var.waf_rate_limit_api
  waf_rate_limit_global = var.waf_rate_limit_global
  apigw_throttle_rate   = var.apigw_throttle_rate
  apigw_throttle_burst  = var.apigw_throttle_burst
}

module "auth" {
  source         = "./modules/auth"
  project        = var.project
  region         = var.region
  ssm_prefix     = local.ssm_prefix
  api_id         = module.edge.api_id
  integration_id = module.edge.integration_id
  cloudfront_url = module.edge.cloudfront_url
}

module "cleanup" {
  source                 = "./modules/cleanup"
  project                = var.project
  vpc_id                 = module.network.vpc_id
  private_subnet_ids     = module.network.private_subnet_ids
  rds_sg_id              = module.security.rds_sg_id
  database_url           = module.data.database_url
  expired_grace_days     = var.cleanup_expired_grace_days
  deleted_retention_days = var.cleanup_deleted_retention_days
  schedule               = var.cleanup_schedule
}

module "cicd" {
  source             = "./modules/cicd"
  project            = var.project
  region             = var.region
  github_repo        = var.github_repo
  ssm_prefix         = local.ssm_prefix
  ecr_repository_arn = module.compute.ecr_repository_arn
}
