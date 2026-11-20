#!/bin/sh
set -eu
umask 077

CA_DIR=${CA_DIR:-/ca}
CERT_DIR=${CERT_DIR:-/certs}
: "${FRONTEND_DOMAIN:?FRONTEND_DOMAIN must be set}"
: "${BACKEND_DOMAIN:?BACKEND_DOMAIN must be set}"

fail() {
    printf 'TLS initialization failed: %s\n' "$*" >&2
    exit 1
}

validate_domain() {
    case "$1" in
        ''|*[!A-Za-z0-9.-]*) fail "Invalid DNS domain: $1" ;;
    esac
    printf '%s\n' "$1" | LC_ALL=C awk -F. '
        length($0) > 253 || NF < 2 { exit 1 }
        { for (i = 1; i <= NF; i++) {
            if (length($i) > 63 || $i !~ /^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$/) exit 1
        } }
    ' || fail "Invalid DNS domain: $1"
}

validate_domain "$FRONTEND_DOMAIN"
validate_domain "$BACKEND_DOMAIN"
mkdir -p "$CA_DIR" "$CERT_DIR"
chmod 700 "$CA_DIR"

tmp_dir=$(mktemp -d "$CERT_DIR/.tls-init.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM

if [ -e "$CA_DIR/ca.key" ] || [ -e "$CA_DIR/ca.crt" ]; then
    [ -f "$CA_DIR/ca.key" ] && [ -f "$CA_DIR/ca.crt" ] \
        || fail 'The persistent CA is incomplete; restore its matching key and certificate.'
    printf 'Reusing the persistent private CA.\n'
else
    printf 'Creating the persistent private CA. Import /certs/ca.crt into LAN clients to trust HTTPS.\n'
    openssl req -x509 -newkey rsa:4096 -nodes -sha256 -days 3650 \
        -subj '/CN=LAN Deployment Root CA' \
        -addext 'basicConstraints=critical,CA:TRUE' \
        -addext 'keyUsage=critical,keyCertSign,cRLSign' \
        -keyout "$tmp_dir/ca.key" -out "$tmp_dir/ca.crt" 2>"$tmp_dir/openssl.log" \
        || { cat "$tmp_dir/openssl.log" >&2; fail 'Could not create the private CA.'; }
    mv "$tmp_dir/ca.key" "$CA_DIR/ca.key"
    mv "$tmp_dir/ca.crt" "$CA_DIR/ca.crt"
fi

ca_public=$(openssl x509 -in "$CA_DIR/ca.crt" -noout -pubkey) \
    || fail 'The persistent CA certificate is invalid.'
ca_key_public=$(openssl pkey -in "$CA_DIR/ca.key" -passin pass: -pubout) \
    || fail 'The persistent CA private key is invalid.'
[ "$ca_public" = "$ca_key_public" ] \
    || fail 'The persistent CA certificate and private key do not match.'
openssl verify -CAfile "$CA_DIR/ca.crt" "$CA_DIR/ca.crt" >/dev/null \
    || fail 'The persistent CA certificate is expired or invalid.'
chmod 600 "$CA_DIR/ca.key"

renew_reason='first issuance'
if [ -e "$CERT_DIR/fullchain.pem" ] || [ -e "$CERT_DIR/privkey.pem" ]; then
    [ -f "$CERT_DIR/fullchain.pem" ] && [ -f "$CERT_DIR/privkey.pem" ] \
        || fail 'The existing server certificate is incomplete.'
    cert_public=$(openssl x509 -in "$CERT_DIR/fullchain.pem" -noout -pubkey) \
        || fail 'The existing server certificate is invalid.'
    cert_key_public=$(openssl pkey -in "$CERT_DIR/privkey.pem" -passin pass: -pubout) \
        || fail 'The existing server private key is invalid.'
    [ "$cert_public" = "$cert_key_public" ] \
        || fail 'The existing server certificate and private key do not match.'
    openssl verify -no_check_time -CAfile "$CA_DIR/ca.crt" "$CERT_DIR/fullchain.pem" >/dev/null \
        || fail 'The existing server certificate was not signed by the persistent CA.'
    cert_sans=$(openssl x509 -in "$CERT_DIR/fullchain.pem" -noout -ext subjectAltName) \
        || fail 'Could not read the existing server certificate SANs.'
    cert_sans=$(printf '%s\n' "$cert_sans" | sed '1d;s/[[:space:]]//g' | tr ',' '\n' | LC_ALL=C sort)
    expected_sans=$(printf 'DNS:%s\nDNS:%s\n' "$FRONTEND_DOMAIN" "$BACKEND_DOMAIN" | LC_ALL=C sort)
    if [ "$cert_sans" != "$expected_sans" ]; then
        renew_reason='deployment domains changed'
    elif ! openssl x509 -in "$CERT_DIR/fullchain.pem" -noout -checkend 2592000 >/dev/null; then
        renew_reason='certificate expires within 30 days'
    else
        renew_reason=''
    fi
fi

if [ -n "$renew_reason" ]; then
    printf 'Issuing HTTPS certificate for %s and %s (%s).\n' "$FRONTEND_DOMAIN" "$BACKEND_DOMAIN" "$renew_reason"
    openssl genrsa -out "$tmp_dir/privkey.pem" 3072
    openssl req -new -sha256 -key "$tmp_dir/privkey.pem" -out "$tmp_dir/server.csr" \
        -subj "/CN=$FRONTEND_DOMAIN" \
        -addext "subjectAltName=DNS:$FRONTEND_DOMAIN,DNS:$BACKEND_DOMAIN" \
        -addext 'basicConstraints=critical,CA:FALSE' \
        -addext 'keyUsage=critical,digitalSignature,keyEncipherment' \
        -addext 'extendedKeyUsage=serverAuth'
    serial=$(openssl rand -hex 16)
    openssl x509 -req -in "$tmp_dir/server.csr" -CA "$CA_DIR/ca.crt" \
        -CAkey "$CA_DIR/ca.key" -set_serial "0x$serial" -days 365 -sha256 \
        -copy_extensions copy -out "$tmp_dir/server.crt"
    openssl verify -CAfile "$CA_DIR/ca.crt" "$tmp_dir/server.crt" >/dev/null
    cat "$tmp_dir/server.crt" "$CA_DIR/ca.crt" >"$tmp_dir/fullchain.pem"
    mv "$tmp_dir/privkey.pem" "$CERT_DIR/privkey.pem"
    mv "$tmp_dir/fullchain.pem" "$CERT_DIR/fullchain.pem"
else
    printf 'Existing HTTPS certificate is valid; reusing it.\n'
fi

cp "$CA_DIR/ca.crt" "$CERT_DIR/ca.crt"
chmod 600 "$CERT_DIR/privkey.pem"
chmod 644 "$CERT_DIR/fullchain.pem" "$CERT_DIR/ca.crt"
printf 'HTTPS certificate initialization complete.\n'
