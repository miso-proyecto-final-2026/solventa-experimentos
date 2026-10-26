variable "region" {
  type    = string
  default = "us-east-1"
}

variable "vpc_name" {
  description = "Tag Name de la VPC de staging (modules/network usa el nombre del clúster)"
  type        = string
  default     = "solventa-staging"
}

variable "instance_type" {
  description = "c5.xlarge: 4 vCPU dedicadas, sin créditos de CPU que se agoten a mitad de corrida"
  type        = string
  default     = "c5.xlarge"
}

variable "volume_gb" {
  description = "La salida cruda de k6 pesa ~500 MB por escenario completo"
  type        = number
  default     = 30
}

variable "k6_version" {
  description = "Misma versión con la que se validaron los scripts en local"
  type        = string
  default     = "0.54.0"
}
