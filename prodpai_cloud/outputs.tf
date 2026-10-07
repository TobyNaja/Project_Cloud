# output "ssh_command" {
#   value       = "ssh -i  ubuntu@${aws_instance.test_falco.public_ip}"
#   description = "Command for SSH into ubuntu"
# }

# output "public_ip" {
#   value = aws_instance.web.public_ip
# }

# output "web_url" {
#   value = "http://${aws_instance.web.public_dns}"
# }

output "lb_domain_name_http" {
  value = "http://${aws_lb.lb.dns_name}/"
}

output "infrastructure_summary" {
  description = "Summary of all provisioned resources for the Web Dashboard"
  value = {
    vpc = {
      id         = aws_vpc.main.id
      cidr_block = aws_vpc.main.cidr_block
    }
    subnets = {
      public  = aws_subnet.public_zone[*].id
      private = [for s in aws_subnet.private_zone : s.id]
    }
    load_balancer = {
      dns_name = aws_lb.lb.dns_name
      arn      = aws_lb.lb.arn
    }
    waf = {
      name = aws_wafv2_web_acl.web_rate_limit.name
    }
    auto_scaling = {
      name             = aws_autoscaling_group.asg.name
      desired_capacity = aws_autoscaling_group.asg.desired_capacity
    }
    target_group = {
      name                 = aws_lb_target_group.lb_target.name
      health_check_path    = aws_lb_target_group.lb_target.health_check[0].path
      health_check_matcher = aws_lb_target_group.lb_target.health_check[0].matcher
      health_check_port    = aws_lb_target_group.lb_target.health_check[0].port
    }
    launch_template = {
      name = aws_launch_template.prodpai_web_template.name
    }
    image_builder = {
      ami_id         = aws_ami_from_instance.web_ami.id
      instance_id    = aws_instance.prodpai_instance.id
      instance_state = aws_instance.prodpai_instance.instance_state
    }
    security_groups = {
      alb        = aws_security_group.lb_sg.id
      instance   = aws_security_group.allow_web.id
      quarantine = aws_security_group.falco_sg.id
    }
    update_time = timestamp()
  }
}
