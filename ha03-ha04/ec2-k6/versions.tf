terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  # Estado local a propósito: este generador vive solo durante la sesión de HA03/HA04 y se destruye
  # al terminar. No usa el bucket de estado de solventa-infra para no mezclar ciclos de vida.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "solventa"
      Environment = "staging"
      Stage       = "ha03-generador"
      ManagedBy   = "terraform"
      Historia    = "HA03"
    }
  }
}
