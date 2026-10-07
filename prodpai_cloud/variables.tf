variable "aws_region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region to deploy into"
}

variable "project_name" {
  type        = string
  default     = "prod-pai"
  description = "Name for prefix of resource tags"
}

variable "environment" {
  type        = string
  default     = "env"
  description = "Deployment environment"
}

variable "cidr_allow_all" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "Allow IP anywhere"
}

variable "instance_type" {
  type        = string
  default     = "t3.small"
  description = "EC2 instance size"

  validation {
    condition     = can(regex("^t[23]\\.", var.instance_type))
    error_message = "Only t2/3 family are permitted on prodpai cloud project"
  }
}

variable "instance_count" {
  type        = number
  default     = 2
  description = "How many Instance to create"
}

variable "extra_tag" {
  type        = map(string)
  default     = {}
  description = "For caller input new tag"
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag to deploy"
}
