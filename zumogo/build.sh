#!/bin/bash
# Compila Zumo Go para las dos arquitecturas y actualiza zumogo.sha256 en la raíz del repo.
# Hace falta Go 1.23 o más. Uso:  bash zumogo/build.sh
set -euo pipefail
cd "$(dirname "$0")"
go vet ./... && go test -count=1 ./...
for a in amd64 arm64; do
	CGO_ENABLED=0 GOOS=linux GOARCH=$a go build -trimpath -buildvcs=false -ldflags "-s -w" -o "../zumogo-$a" .
done
cd ..
sha256sum zumogo-amd64 zumogo-arm64 > zumogo.sha256
cat zumogo.sha256
