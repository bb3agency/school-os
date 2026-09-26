# Pre-token-generation Lambda (ADR-0018): adds sos:mfa to tokens of both pools. Requires the ESSENTIALS or
# PLUS feature plan (access-token customisation, trigger event V2_0).

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "archive_file" "pre_token" {
  type        = "zip"
  source_file = "${path.module}/lambda/pre_token_generation.py"
  output_path = "${path.module}/.build/pre_token_generation.zip"
}

data "aws_iam_policy_document" "pre_token_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pre_token" {
  name               = "${var.name_prefix}-cognito-pre-token"
  assume_role_policy = data.aws_iam_policy_document.pre_token_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "pre_token" {
  # Wildcard over this account's pools avoids a pool <-> function dependency cycle; the function only
  # reads the MFA settings of the user the token is being issued to.
  statement {
    sid       = "ReadMfaSettings"
    actions   = ["cognito-idp:AdminGetUser"]
    resources = ["arn:${data.aws_partition.current.partition}:cognito-idp:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:userpool/*"]
  }

  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.pre_token.arn}:*"]
  }
}

resource "aws_iam_role_policy" "pre_token" {
  name   = "pre-token-generation"
  role   = aws_iam_role.pre_token.id
  policy = data.aws_iam_policy_document.pre_token.json
}

resource "aws_cloudwatch_log_group" "pre_token" {
  name              = "/aws/lambda/${var.name_prefix}-cognito-pre-token"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.logs_kms_key_arn
  tags              = var.tags
}

resource "aws_lambda_function" "pre_token" {
  function_name    = "${var.name_prefix}-cognito-pre-token"
  description      = "Adds sos:mfa claim to Cognito tokens (ADR-0018)"
  role             = aws_iam_role.pre_token.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  handler          = "pre_token_generation.handler"
  filename         = data.archive_file.pre_token.output_path
  source_code_hash = data.archive_file.pre_token.output_base64sha256
  timeout          = 5
  memory_size      = 128

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.pre_token.name
  }

  tags = var.tags

  depends_on = [aws_iam_role_policy.pre_token]
}

resource "aws_lambda_permission" "cognito" {
  for_each = local.pools

  statement_id  = "AllowCognito-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pre_token.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.this[each.key].arn
}
