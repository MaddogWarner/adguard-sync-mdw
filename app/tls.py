from __future__ import annotations

import ipaddress
import logging
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.config import AppConfig

logger = logging.getLogger(__name__)

_SELF_SIGNED_DAYS = 3650


class TlsError(RuntimeError):
    """Raised when TLS material cannot be located or created."""


@dataclass(frozen=True)
class TlsFiles:
    cert_file: str
    key_file: str


def resolve_tls(config: AppConfig) -> TlsFiles | None:
    """Return the cert/key to serve with, or None when TLS is disabled.

    A user-provided cert+key is used as-is; otherwise a self-signed pair is
    generated (and reused) under the data directory.
    """
    tls = config.tls
    if not tls.enabled:
        logger.info("tls_disabled")
        return None

    if tls.cert_file and tls.key_file:
        cert = Path(tls.cert_file)
        key = Path(tls.key_file)
        missing = [str(path) for path in (cert, key) if not path.is_file()]
        if missing:
            raise TlsError(f"tls cert/key not found: {', '.join(missing)}")
        logger.info("tls_provided_cert", extra={"cert_file": str(cert)})
        return TlsFiles(str(cert), str(key))

    cert_dir = Path(config.database_path).parent / "certs"
    cert = cert_dir / "selfsigned.crt"
    key = cert_dir / "selfsigned.key"
    ensure_self_signed(cert, key)
    logger.info("tls_self_signed_cert", extra={"cert_file": str(cert)})
    return TlsFiles(str(cert), str(key))


def ensure_self_signed(cert_path: Path, key_path: Path) -> None:
    """Create a self-signed cert/key pair if one is missing or expired."""
    if cert_path.is_file() and key_path.is_file() and not _is_expired(cert_path):
        return
    generate_self_signed(cert_path, key_path)


def _is_expired(cert_path: Path) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (ValueError, OSError):
        return True
    return cert.not_valid_after_utc <= datetime.now(UTC)


def generate_self_signed(cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hostname = socket.gethostname() or "adguard-sync"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "adguard-sync")])
    alt_names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    if hostname not in {"localhost", "adguard-sync"}:
        alt_names.append(x509.DNSName(hostname))

    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=_SELF_SIGNED_DAYS))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Write the key with owner-only permissions before populating it.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key_bytes)
    logger.info("tls_self_signed_generated", extra={"cert_file": str(cert_path)})
