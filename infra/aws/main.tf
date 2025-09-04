provider "aws" {
  region = var.region
}

resource "random_string" "sfx" {
  length  = 6
  upper   = false
  special = false
}

locals {
  bucket_name = "${var.project}-bucket-${random_string.sfx.result}"
  table_name  = "egrid_plants"
}

# ----------------- S3 -----------------
resource "aws_s3_bucket" "data" {
  bucket = local.bucket_name
}

resource "aws_s3_bucket_public_access_block" "block" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Create zero-byte "folder markers" (works on Windows)
resource "aws_s3_object" "incoming_prefix" {
  bucket  = aws_s3_bucket.data.id
  key     = "incoming/"
  content = ""
}

resource "aws_s3_object" "processed_prefix" {
  bucket  = aws_s3_bucket.data.id
  key     = "processed/"
  content = ""
}

# ----------------- DynamoDB -----------------
resource "aws_dynamodb_table" "plants" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "plant_id"

  attribute {
    name = "plant_id"
    type = "S"
  }
}

# ----------------- IAM (Lambda roles) -----------------
data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest_role" {
  name               = "${var.project}-ingest-role"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

resource "aws_iam_role" "api_role" {
  name               = "${var.project}-api-role"
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

# CloudWatch Logs basic for both
resource "aws_iam_role_policy_attachment" "ingest_logs" {
  role       = aws_iam_role.ingest_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "api_logs" {
  role       = aws_iam_role.api_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Ingest permissions: S3 + DynamoDB write
data "aws_iam_policy_document" "ingest_access" {
  statement {
    actions   = ["dynamodb:PutItem", "dynamodb:DescribeTable"]
    resources = [aws_dynamodb_table.plants.arn]
  }

  statement {
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket", "s3:CopyObject"]
    resources = [
      aws_s3_bucket.data.arn,
      "${aws_s3_bucket.data.arn}/*"
    ]
  }
}

resource "aws_iam_policy" "ingest_access" {
  name   = "${var.project}-ingest-access"
  policy = data.aws_iam_policy_document.ingest_access.json
}

resource "aws_iam_policy_attachment" "ingest_attach" {
  name       = "${var.project}-ingest-attach"
  roles      = [aws_iam_role.ingest_role.name]
  policy_arn = aws_iam_policy.ingest_access.arn
}

# API permissions: DynamoDB read
data "aws_iam_policy_document" "api_access" {
  statement {
    actions   = ["dynamodb:Scan", "dynamodb:DescribeTable"]
    resources = [aws_dynamodb_table.plants.arn]
  }
}

resource "aws_iam_policy" "api_access" {
  name   = "${var.project}-api-access"
  policy = data.aws_iam_policy_document.api_access.json
}

resource "aws_iam_policy_attachment" "api_attach" {
  name       = "${var.project}-api-attach"
  roles      = [aws_iam_role.api_role.name]
  policy_arn = aws_iam_policy.api_access.arn
}

# ----------------- Lambda code packaging -----------------
# (these folders must exist with lambda_function.py inside each)
data "archive_file" "ingest_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_ingest"
  output_path = "${path.module}/lambda_ingest.zip"
}

data "archive_file" "api_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_api"
  output_path = "${path.module}/lambda_api.zip"
}

# ----------------- Lambdas -----------------
resource "aws_lambda_function" "ingest" {
  function_name = "${var.project}-ingest"
  role          = aws_iam_role.ingest_role.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.12"

  filename         = data.archive_file.ingest_zip.output_path
  source_code_hash = data.archive_file.ingest_zip.output_base64sha256

  environment {
    variables = {
      TABLE_NAME          = aws_dynamodb_table.plants.name
      S3_BUCKET           = aws_s3_bucket.data.bucket
      S3_INCOMING_PREFIX  = "incoming/"
      S3_PROCESSED_PREFIX = "processed/"
    }
  }
}

resource "aws_lambda_function" "api" {
  function_name = "${var.project}-api"
  role          = aws_iam_role.api_role.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.12"

  filename         = data.archive_file.api_zip.output_path
  source_code_hash = data.archive_file.api_zip.output_base64sha256

  environment {
    variables = {
      TABLE_NAME = aws_dynamodb_table.plants.name
    }
  }
}

# Allow S3 to invoke ingest Lambda
resource "aws_lambda_permission" "s3_invoke" {
  statement_id  = "AllowExecutionFromS3"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.data.arn
}

# Bucket notification -> trigger ingest on CSV create in incoming/
resource "aws_s3_bucket_notification" "notify" {
  bucket = aws_s3_bucket.data.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "incoming/"
    filter_suffix       = ".csv"
  }

  depends_on = [aws_lambda_permission.s3_invoke]
}

# ----------------- API Gateway (HTTP API) -----------------
resource "aws_apigatewayv2_api" "http" {
  name          = "${var.project}-http"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "OPTIONS"]
    allow_headers = ["*"]
  }
}

resource "aws_apigatewayv2_integration" "api_lambda" {
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "top" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /top"
  target    = "integrations/${aws_apigatewayv2_integration.api_lambda.id}"
}

resource "aws_apigatewayv2_route" "search" {
  api_id    = aws_apigatewayv2_api.http.id
  route_key = "GET /search"
  target    = "integrations/${aws_apigatewayv2_integration.api_lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
}

# Allow API Gateway to invoke API Lambda
resource "aws_lambda_permission" "apigw_invoke" {
  statement_id  = "AllowAPIGWInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
########################
# Static site (S3 + CloudFront)
########################

# Private bucket for the site
resource "aws_s3_bucket" "site" {
  bucket = "${var.project}-web-${random_string.sfx.result}"
}

resource "aws_s3_bucket_public_access_block" "site_block" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# CloudFront Origin Access Control (OAC)
resource "aws_cloudfront_origin_access_control" "oac" {
  name                              = "${var.project}-site-oac"
  description                       = "OAC for S3 private site bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# CloudFront distribution pointing to S3 bucket
resource "aws_cloudfront_distribution" "cdn" {
  enabled             = true
  default_root_object = "index.html"

  origin {
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_id                = "site-s3-origin"
    origin_access_control_id = aws_cloudfront_origin_access_control.oac.id
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "site-s3-origin"
    viewer_protocol_policy = "redirect-to-https"

    forwarded_values {
      query_string = true
      cookies { forward = "none" }
    }
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

# Allow CloudFront (OAC) to read from the bucket
data "aws_iam_policy_document" "site_policy" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.cdn.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site_read" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site_policy.json
}

# Upload the HTML (templated with API URL)
resource "aws_s3_object" "site_index" {
  bucket = aws_s3_bucket.site.id
  key    = "index.html"
  content = replace(
    file("${path.module}/site/index.html"),
    "__API__",
    aws_apigatewayv2_api.http.api_endpoint
  )
  content_type = "text/html"
}
