import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ("crawler", "ontology", "atlas", "graph", "rag", "vault")
SUFFIXES = {
    "FRONTEND_DOMAIN_SUFFIX": "asiral-helio.internal",
    "BACKEND_DOMAIN_SUFFIX": "asiral-helio.api",
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
            ca_hash = hashlib.sha256(ca.read_bytes()).hexdigest()
            certificate_hash = hashlib.sha256(certificate.read_bytes()).hexdigest()
            run(command, env=env)
            self.assertEqual(certificate_hash, hashlib.sha256(certificate.read_bytes()).hexdigest())
            for suffix in SUFFIXES.values():
                for project in PROJECTS:
                    run(["openssl", "verify", "-purpose", "sslserver", "-verify_hostname", f"{project}.{suffix}",
                         "-CAfile", str(ca), str(certificate)])
                for invalid in (suffix, f"nested.crawler.{suffix}"):
                    result = run(["openssl", "verify", "-verify_hostname", invalid, "-CAfile", str(ca),
                                  str(certificate)], check=False)
                    self.assertNotEqual(result.returncode, 0)

            # Replace the wildcard leaf with the previous single-project SAN contract.
            csr = path / "previous.csr"
            run(["openssl", "req", "-new", "-key", str(path / "certs/privkey.pem"), "-out", str(csr),
                 "-subj", "/CN=ontology.asiral-helio.internal", "-addext",
                 "subjectAltName=DNS:ontology.asiral-helio.internal,DNS:ontology.asiral-helio.api"])
            run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(path / "ca/ca.key"),
                 "-set_serial", "42", "-days", "365", "-copy_extensions", "copy", "-out", str(certificate)])
            result = run(command, env=env)
            self.assertIn("deployment domains changed", result.stdout)
            self.assertEqual(ca_hash, hashlib.sha256(ca.read_bytes()).hexdigest())
            sans = run(["openssl", "x509", "-in", str(certificate), "-noout", "-ext", "subjectAltName"]).stdout
            self.assertIn("DNS:*.asiral-helio.internal", sans)
            self.assertIn("DNS:*.asiral-helio.api", sans)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_runtime_config_routes_each_project_to_its_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runtime-config.js"
            script = (ROOT / "docker/nginx-runtime.envsh").read_text().replace(
                "/etc/nginx/runtime-config.js", str(output),
            )
            for port in ("443", "8443"):
                run(["sh", "-c", script], env={**os.environ, **SUFFIXES, "SPIDER_HTTPS_PORT": port})
                for project in PROJECTS:
                    result = run([
                        "node", "-e",
                        'const window = {location: {hostname: process.argv[1]}}; '
                        'require("vm").runInNewContext(require("fs").readFileSync(0, "utf8"), {window}); '
                        'console.log(JSON.stringify(window.__APP_CONFIG__));',
                        f"{project}.asiral-helio.internal",
                    ], input=output.read_text())
                    config = json.loads(result.stdout)
                    port_suffix = "" if port == "443" else f":{port}"
                    self.assertEqual(config["apiOrigin"], f"https://{project}.asiral-helio.api{port_suffix}")
                    self.assertEqual(config["frontendDomainSuffix"], "asiral-helio.internal")

    def test_suffix_injection_and_invalid_dns_labels_are_rejected(self):
        for suffix in ("valid.internal\ninvalid", "bad-.internal", "example.internal;return 200;"):
            env = {**os.environ, **SUFFIXES, "FRONTEND_DOMAIN_SUFFIX": suffix, "SPIDER_HTTPS_PORT": "443"}
            for name in ("init-tls.sh", "nginx-runtime.envsh"):
                result = run(["sh", str(ROOT / "docker" / name)], env=env, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Invalid", result.stderr)


if __name__ == "__main__":
    unittest.main()
