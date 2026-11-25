"""Credential encryption shared by the connection registry and business workers."""

import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def connection_cipher(secret: str) -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b"ontology-data-connections:v1").derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))
