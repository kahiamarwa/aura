#!/bin/bash
# Generate self-signed certificate for HTTPS on IP
mkdir -p certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/selfsigned.key \
  -out certs/selfsigned.crt \
  -subj "/C=FR/ST=Paris/L=Paris/O=AURA/CN=88.223.94.178" \
  -addext "subjectAltName=IP:88.223.94.178"
echo "Certificat genere dans nginx/certs/"
