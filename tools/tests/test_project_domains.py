import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
DOMAIN_SUFFIX = "astral.io"
SUFFIXES = {
    "FRONTEND_DOMAIN_SUFFIX": DOMAIN_SUFFIX,
    "BACKEND_DOMAIN_SUFFIX": DOMAIN_SUFFIX,
}


def run(command, *, env=None, check=True, input=None):
    return subprocess.run(command, env=env, check=check, input=input, capture_output=True, text=True)


class ProjectDomainsTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
    def test_wildcard_tls_migration_reuses_ca_and_existing_certificate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            env = {**os.environ, **SUFFIXES, "CA_DIR": str(path / "ca"), "CERT_DIR": str(path / "certs")}
            command = ["sh", str(ROOT / "docker/init-tls.sh")]
            run(command, env=env)
            ca = path / "ca/ca.crt"
            certificate = path / "certs/fullchain.pem"
            for cert in (ca, certificate):
                self.assertEqual(run(["openssl", "x509", "-in", str(cert), "-noout", "-enddate"]).stdout.strip(),
                                 "notAfter=Dec 31 23:59:59 2099 GMT")
            ca_hash = hashlib.sha256(ca.read_bytes()).hexdigest()
            certificate_hash = hashlib.sha256(certificate.read_bytes()).hexdigest()
            run(command, env=env)
            self.assertEqual(certificate_hash, hashlib.sha256(certificate.read_bytes()).hexdigest())
            domains = [f"app.{SUFFIXES['FRONTEND_DOMAIN_SUFFIX']}", f"api.{SUFFIXES['BACKEND_DOMAIN_SUFFIX']}"]
            for domain in domains:
                run(["openssl", "verify", "-purpose", "sslserver", "-verify_hostname", domain,
                     "-CAfile", str(ca), str(certificate)])
            for suffix in SUFFIXES.values():
                for invalid in (suffix, f"nested.crawler.{suffix}"):
                    result = run(["openssl", "verify", "-verify_hostname", invalid, "-CAfile", str(ca),
                                  str(certificate)], check=False)
                    self.assertNotEqual(result.returncode, 0)

            # Replace the wildcard leaf with the previous per-project SAN contract.
            csr = path / "previous.csr"
            run(["openssl", "req", "-new", "-key", str(path / "certs/privkey.pem"), "-out", str(csr),
                 "-subj", "/CN=app.astral-helio.internal", "-addext",
                 "subjectAltName=DNS:*.astral-helio.internal,DNS:*.astral-helio.api"])
            run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(path / "ca/ca.key"),
                 "-set_serial", "42", "-days", "365", "-copy_extensions", "copy", "-out", str(certificate)])
            result = run(command, env=env)
            self.assertIn("deployment domains changed", result.stdout)
            self.assertEqual(ca_hash, hashlib.sha256(ca.read_bytes()).hexdigest())
            sans = run(["openssl", "x509", "-in", str(certificate), "-noout", "-ext", "subjectAltName"]).stdout
            self.assertIn("DNS:*.astral.io", sans)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_runtime_config_contains_project_independent_deployment_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runtime-config.js"
            script = (ROOT / "docker/nginx-runtime.envsh").read_text().replace(
                "/etc/nginx/runtime-config.js", str(output),
            )
            for port in ("443", "8443"):
                run(["sh", "-c", script], env={**os.environ, **SUFFIXES, "SPIDER_HTTPS_PORT": port})
                result = run([
                    "node", "-e",
                    'const window = {}; '
                    'require("vm").runInNewContext(require("fs").readFileSync(0, "utf8"), {window}); '
                    'console.log(JSON.stringify(window.__APP_CONFIG__));',
                ], input=output.read_text())
                self.assertEqual(json.loads(result.stdout), {
                    "backendDomainSuffix": "astral.io",
                    "httpsPort": port,
                    "frontendDomainSuffix": "astral.io",
                })

    def test_suffix_injection_and_invalid_dns_labels_are_rejected(self):
        for suffix in ("valid.internal\ninvalid", "bad-.internal", "example.internal;return 200;"):
            env = {**os.environ, **SUFFIXES, "FRONTEND_DOMAIN_SUFFIX": suffix, "SPIDER_HTTPS_PORT": "443"}
            for name in ("init-tls.sh", "nginx-runtime.envsh"):
                result = run(["sh", str(ROOT / "docker" / name)], env=env, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid", result.stderr)


if __name__ == "__main__":
    unittest.main()
