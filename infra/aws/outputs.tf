output "bucket_name" { value = aws_s3_bucket.data.bucket }
output "dynamodb_table" { value = aws_dynamodb_table.plants.name }
output "api_invoke_url" { value = aws_apigatewayv2_api.http.api_endpoint }
output "site_bucket" {
  value = aws_s3_bucket.site.bucket
}

output "cloudfront_url" {
  value = "https://${aws_cloudfront_distribution.cdn.domain_name}"
}

