variable "ANOMALY_IMAGE" {
  default = "clickathon-anomaly-detector:latest"
}

group "default" {
  targets = ["anomaly-detector"]
}

target "anomaly-detector" {
  context    = "."
  dockerfile = "Dockerfile"
  tags       = ["${ANOMALY_IMAGE}"]
  platforms  = ["linux/amd64", "linux/arm64"]
}
