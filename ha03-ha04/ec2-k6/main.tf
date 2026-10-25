# Generador de carga de HA03 y HA04 dentro de la VPC de staging (HA03 c2: generador dentro de la región; llama al
# NLB interno, sin pasar por el borde). Sin SSH ni IP pública: se opera por SSM Session Manager y la evidencia sale
# por un bucket S3 temporal.

data "aws_caller_identity" "current" {}

data "aws_vpc" "staging" {
  filter {
    name   = "tag:Name"
    values = [var.vpc_name]
  }
}

data "aws_subnets" "privadas" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.staging.id]
  }
  filter {
    name   = "tag:kubernetes.io/role/internal-elb"
    values = ["1"]
  }
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# --- Bucket de evidencia y scripts ---------------------------------------------------------

resource "aws_s3_bucket" "evidencia" {
  bucket        = "solventa-ha03-evidencia-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # temporal: la evidencia se descarga antes del destroy
}

resource "aws_s3_bucket_public_access_block" "evidencia" {
  bucket                  = aws_s3_bucket.evidencia.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "evidencia" {
  bucket = aws_s3_bucket.evidencia.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

locals {
  scripts = {
    "scripts/ha03-autoescalado.js" = "${path.module}/../scripts/ha03-autoescalado.js"
    "scripts/ha04-trafico.js"      = "${path.module}/../scripts/ha04-trafico.js"
    "scripts/analizar-ha03.py"     = "${path.module}/../scripts/analizar-ha03.py"
    "scripts/analizar-ha04.py"     = "${path.module}/../scripts/analizar-ha04.py"
    "scripts/sembrar-clientes.py"  = "${path.module}/../../ha01-ronda2/seed/sembrar-clientes.py"
    "scripts/correr-escenario.sh"  = "${path.module}/correr-escenario.sh"
  }
}

resource "aws_s3_object" "scripts" {
  for_each = local.scripts

  bucket = aws_s3_bucket.evidencia.id
  key    = each.key
  source = each.value
  etag   = filemd5(each.value)
}

# --- Identidad de la instancia ------------------------------------------------------------

resource "aws_iam_role" "generador" {
  name_prefix = "solventa-ha03-k6-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.generador.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "evidencia" {
  name = "evidencia-ha03"
  role = aws_iam_role.generador.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.evidencia.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.evidencia.arn
      },
    ]
  })
}

resource "aws_iam_instance_profile" "generador" {
  name_prefix = "solventa-ha03-k6-"
  role        = aws_iam_role.generador.name
}

# --- Red: solo salida (NLB interno, SSM, S3 y descarga de k6 por el NAT) -----------------

resource "aws_security_group" "generador" {
  name_prefix = "solventa-ha03-k6-"
  description = "Generador k6 de HA03 y HA04: sin entrada, salida libre"
  vpc_id      = data.aws_vpc.staging.id
}

resource "aws_vpc_security_group_egress_rule" "todo" {
  security_group_id = aws_security_group.generador.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

# --- Instancia ---------------------------------------------------------------------------

resource "aws_instance" "generador" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = sort(data.aws_subnets.privadas.ids)[0]
  vpc_security_group_ids = [aws_security_group.generador.id]
  iam_instance_profile   = aws_iam_instance_profile.generador.name

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size = var.volume_gb
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    k6_version = var.k6_version
    bucket     = aws_s3_bucket.evidencia.id
  })
  user_data_replace_on_change = true

  tags = { Name = "solventa-ha03-k6" }

  depends_on = [aws_s3_object.scripts, aws_iam_role_policy.evidencia]
}
