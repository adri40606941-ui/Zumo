#!/bin/bash
# Compila zumoid (control de dispositivo) para las dos arquitecturas y actualiza zumoid.sha256
# en la raíz del repo. Hace falta Go 1.23 o más. Uso:  bash zumoid/build.sh
set -euo pipefail
cd "$(dirname "$0")"
go vet ./... && go test -count=1 ./...
for a in amd64 arm64; do
	CGO_ENABLED=0 GOOS=linux GOARCH=$a go build -trimpath -buildvcs=false -ldflags "-s -w" -o "../zumoid-$a" .
done
cd ..
sha256sum zumoid-amd64 zumoid-arm64 > zumoid.sha256
cat zumoid.sha256
