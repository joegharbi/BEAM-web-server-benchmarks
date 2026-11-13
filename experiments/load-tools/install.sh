#!/usr/bin/env bash
# Installs the pinned load tools into ./bin (not tracked by git).
# Needs: curl, tar, git, gcc, make, libssl-dev, zlib1g-dev. Linux x86_64 only.
set -euo pipefail

OHA=1.16.0      # https://github.com/hatoo/oha (no checksum file, hash checked below)
K6=2.4.0       # https://github.com/grafana/k6
VEGETA=12.13.0  # https://github.com/tsenart/vegeta
WRK2=44a94c17d8e6a0bac8559b53da76848e430cb7a7  # https://github.com/giltene/wrk2 (no releases, last commit)

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HERE/bin"
WORK="$(mktemp -d)"   # wrk2's Makefile breaks on paths with spaces, so build outside the repo
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$BIN"
cd "$WORK"

curl -sSLo oha "https://github.com/hatoo/oha/releases/download/v$OHA/oha-linux-amd64"
echo "620bb9e16fb53eabc9a3fc45f88bdb41fefa3fee5c05e75892011ce320391716  oha" | sha256sum -c

curl -sSLO "https://github.com/grafana/k6/releases/download/v$K6/k6-v$K6-linux-amd64.tar.gz"
curl -sSLO "https://github.com/grafana/k6/releases/download/v$K6/k6-v$K6-checksums.txt"
grep "linux-amd64.tar.gz" "k6-v$K6-checksums.txt" | sha256sum -c
tar xzf "k6-v$K6-linux-amd64.tar.gz"

curl -sSLO "https://github.com/tsenart/vegeta/releases/download/v$VEGETA/vegeta_${VEGETA}_linux_amd64.tar.gz"
curl -sSLO "https://github.com/tsenart/vegeta/releases/download/v$VEGETA/vegeta_${VEGETA}_checksums.txt"
grep "linux_amd64.tar.gz" "vegeta_${VEGETA}_checksums.txt" | sha256sum -c
tar xzf "vegeta_${VEGETA}_linux_amd64.tar.gz" vegeta

git clone -q https://github.com/giltene/wrk2.git
git -C wrk2 checkout -q "$WRK2"
make -C wrk2 -j4 > wrk2-build.log 2>&1

install -m 755 oha "$BIN/oha"
install -m 755 "k6-v$K6-linux-amd64/k6" "$BIN/k6"
install -m 755 vegeta "$BIN/vegeta"
install -m 755 wrk2/wrk "$BIN/wrk2"

"$BIN/oha" --version
"$BIN/k6" version
"$BIN/vegeta" -version | head -1
"$BIN/wrk2" -v 2>&1 | head -1 || true
