#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
OUT="$ROOT/testdata"
mkdir -p "$OUT"
cd "$OUT"

gen_ca() {
  name=$1
  cn=$2
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$name.key" -out "$name.pem" -days 3650 \
    -subj "/CN=$cn" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"
}

gen_leaf() {
  name=$1
  cn=$2
  ca=$3
  extra=${4:-}
  openssl req -newkey rsa:2048 -nodes \
    -keyout "$name.key" -out "$name.csr" -subj "/CN=$cn"
  ext="$name.ext"
  {
    echo "subjectAltName=DNS:localhost,IP:127.0.0.1"
    echo "basicConstraints=CA:FALSE"
    echo "keyUsage=digitalSignature,keyEncipherment"
    echo "extendedKeyUsage=serverAuth,clientAuth"
    if [ -n "$extra" ]; then
      printf "%s\n" "$extra"
    fi
  } >"$ext"
  openssl x509 -req -in "$name.csr" \
    -CA "$ca.pem" -CAkey "$ca.key" -CAcreateserial \
    -out "$name.pem" -days 3650 -extfile "$ext"
  rm -f "$name.csr" "$ext"
}

gen_ca server-ca "test-server-ca"
gen_ca client-ca "test-client-ca"
gen_ca unrelated-ca "test-unrelated-ca"

# Intermediate client CA under client-ca.
openssl req -newkey rsa:2048 -nodes \
  -keyout client-int.key -out client-int.csr -subj "/CN=test-client-int"
cat >client-int.ext <<'EOF'
basicConstraints=critical,CA:TRUE,pathlen:0
keyUsage=critical,keyCertSign,cRLSign
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF
openssl x509 -req -in client-int.csr \
  -CA client-ca.pem -CAkey client-ca.key -CAcreateserial \
  -out client-int.pem -days 3650 -extfile client-int.ext
rm -f client-int.csr client-int.ext

gen_leaf server localhost server-ca
gen_leaf client client-one client-ca
gen_leaf client-unrelated client-unrelated unrelated-ca
gen_leaf client-via-int client-via-int client-int

# Chain: leaf + intermediate. Leaf-only is client-via-int.pem itself.
cat client-via-int.pem client-int.pem >client-chain.pem

# Expired client certificate. OpenSSL 3.0 `x509 -req` does not accept
# -not_before/-not_after; `openssl ca` startdate/enddate does.
openssl req -newkey rsa:2048 -nodes \
  -keyout client-expired.key -out client-expired.csr -subj "/CN=client-expired"
CA_WORK="$OUT/.ca-expired"
mkdir -p "$CA_WORK/newcerts"
: >"$CA_WORK/index.txt"
printf '01\n' >"$CA_WORK/serial"
cat >"$CA_WORK/ca.cnf" <<EOF
[ ca ]
default_ca = CA_default
[ CA_default ]
dir = $CA_WORK
database = \$dir/index.txt
serial = \$dir/serial
new_certs_dir = \$dir/newcerts
default_md = sha256
policy = policy_any
x509_extensions = usr_cert
unique_subject = no
email_in_dn = no
[ policy_any ]
commonName = supplied
[ usr_cert ]
basicConstraints = CA:FALSE
extendedKeyUsage = clientAuth
subjectAltName = DNS:localhost,IP:127.0.0.1
EOF
openssl ca -batch -notext -config "$CA_WORK/ca.cnf" \
  -cert client-ca.pem -keyfile client-ca.key \
  -in client-expired.csr -out client-expired.pem \
  -startdate 20010101000000Z -enddate 20010102000000Z
if openssl x509 -in client-expired.pem -checkend 0 >/dev/null 2>&1; then
  echo "client-expired.pem is not expired" >&2
  exit 1
fi
rm -rf "$CA_WORK" client-expired.csr

# Mismatched key: another unused key.
openssl genrsa -out client-mismatch.key 2048

# Encrypted PKCS#8 copy of the valid client key.
openssl pkcs8 -topk8 -in client.key -out client-encrypted.key \
  -v2 aes-256-cbc -passout pass:secret

printf 'this is not a pem certificate\n' >corrupt.pem

rm -f ./*.csr ./*.srl ./*.ext
ln -sfn ../testdata "$ROOT/src/testdata"
echo "generated testdata in $OUT"
