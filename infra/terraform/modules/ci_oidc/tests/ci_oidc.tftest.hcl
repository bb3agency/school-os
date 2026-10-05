# CI access via GitHub OIDC only, bound to bb3agency/school-os and a GitHub Environment (07 §13).

mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111122223333" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
}

variables {
  name_prefix         = "sos-test"
  deploy_environment  = "production"
  ecr_repository_arns = ["arn:aws:ecr:ap-south-1:111122223333:repository/schoolos/api"]
  ecs_cluster_arn     = "arn:aws:ecs:ap-south-1:111122223333:cluster/sos-test"
  passable_role_arns  = ["arn:aws:iam::111122223333:role/sos-test-api-task"]
}

run "prod_deploy_requires_environment" {
  command = plan

  assert {
    condition     = output.deploy_subjects == ["repo:bb3agency/school-os:environment:production"]
    error_message = "Prod deploy role trusts only the protected production environment of bb3agency/school-os."
  }

  assert {
    condition     = aws_iam_openid_connect_provider.github[0].url == "https://token.actions.githubusercontent.com" && aws_iam_openid_connect_provider.github[0].client_id_list == toset(["sts.amazonaws.com"])
    error_message = "GitHub OIDC provider with sts audience."
  }
}

run "staging_may_deploy_from_main" {
  command = plan

  variables {
    deploy_environment = "staging"
    allow_main_branch  = true
  }

  assert {
    condition     = contains(output.deploy_subjects, "repo:bb3agency/school-os:ref:refs/heads/main") && length(output.deploy_subjects) == 2
    error_message = "Staging also trusts main-branch pushes."
  }
}

run "apply_role_optional" {
  command = plan

  assert {
    condition     = length(aws_iam_role.apply) == 0
    error_message = "The administrative apply role is opt-in."
  }
}

# SEC-009: the plan role trusts every same-repo pull request, so it may write only the S3-native
# lock files, never the state that the apply role later trusts.
run "plan_role_cannot_write_state" {
  command = plan

  variables {
    state_bucket_arn = "arn:aws:s3:::sos-test-tfstate"
  }

  assert {
    condition = alltrue(flatten([
      for s in data.aws_iam_policy_document.plan.statement : [
        for r in s.resources : endswith(r, ".tflock")
      ] if length(setintersection(toset(s.actions), toset(["s3:PutObject", "s3:DeleteObject", "s3:*"]))) > 0
    ]))
    error_message = "The plan role may put or delete only *.tflock objects in the state bucket."
  }

  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.plan.statement :
      contains(s.actions, "s3:PutObject") && contains(s.resources, "arn:aws:s3:::sos-test-tfstate/*.tflock")
    ])
    error_message = "The plan role can still take the S3-native state lock."
  }
}

# Audit W3-04: a role that every same-repo pull request can assume never reads secrets.
run "pr_plan_role_reads_no_secrets" {
  command = plan

  assert {
    condition     = toset(output.plan_subjects) == toset(["repo:bb3agency/school-os:pull_request", "repo:bb3agency/school-os:ref:refs/heads/main"])
    error_message = "Without plan_environment the plan role trusts pull requests and main."
  }

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.plan.statement : !contains(s.actions, "secretsmanager:GetSecretValue")
    ])
    error_message = "The pull-request plan role cannot read any secret."
  }
}

run "pr_plan_role_cannot_be_given_secrets" {
  command = plan

  variables {
    plan_can_read_secrets = true
  }

  expect_failures = [var.plan_can_read_secrets]
}

run "gated_plan_role_may_refresh_secrets" {
  command = plan

  variables {
    plan_environment      = "staging-plan"
    plan_can_read_secrets = true
  }

  assert {
    condition     = toset(output.plan_subjects) == toset(["repo:bb3agency/school-os:environment:staging-plan"])
    error_message = "A gated plan role trusts only its GitHub Environment (required reviewers), never pull_request."
  }

  assert {
    condition = anytrue([
      for s in data.aws_iam_policy_document.plan.statement : contains(s.actions, "secretsmanager:GetSecretValue")
    ])
    error_message = "Only the environment-gated plan role refreshes secret versions."
  }
}
