#!/bin/sh
# Makes headless Chromium trust the egress proxy CA of the Claude Code cloud image, with TLS verification ON.
# (The image documents an NSS store for browsers, but on a fresh container it is empty: every https page fails
# with ERR_CERT_AUTHORITY_INVALID.) Safe to run repeatedly. Needs apt for libnss3-tools (certutil).
set -e
CA=/root/.ccr/agent-proxy-ca.crt
[ -f "$CA" ] || exit 0                       # not behind the proxy: nothing to do
if ! command -v certutil >/dev/null 2>&1; then
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y -qq libnss3-tools >/dev/null 2>&1
fi
mkdir -p "$HOME/.pki/nssdb"
[ -f "$HOME/.pki/nssdb/cert9.db" ] || certutil -N -d "sql:$HOME/.pki/nssdb" --empty-password
certutil -A -d "sql:$HOME/.pki/nssdb" -n ccr-agent-proxy -t "C,," -i "$CA"
echo "browser trust ready"
