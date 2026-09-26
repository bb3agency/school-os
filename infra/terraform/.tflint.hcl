# tflint configuration for all SchoolOS Terraform (run from infra/terraform):
#   tflint --init && tflint --recursive --config "$(pwd)/.tflint.hcl"
config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "all"
}

plugin "aws" {
  enabled = true
  version = "0.49.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}

# Every module pins the provider exactly in its own versions.tf; a separate lock per module is
# intentionally not committed (see scripts/validate.sh).
rule "terraform_standard_module_structure" {
  enabled = false
}
