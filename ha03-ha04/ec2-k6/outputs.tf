output "instance_id" {
  value = aws_instance.generador.id
}

output "availability_zone" {
  description = "AZ del generador; se documenta junto con la de las réplicas (criterio 6)"
  value       = aws_instance.generador.availability_zone
}

output "bucket_evidencia" {
  value = aws_s3_bucket.evidencia.id
}

output "conectar" {
  value = "aws ssm start-session --region ${var.region} --target ${aws_instance.generador.id}"
}
