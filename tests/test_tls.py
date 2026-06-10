from __future__ import annotations

import stat

import pytest
from cryptography import x509

from app.config import AppConfig, HostConfig, TlsConfig
from app.tls import TlsError, generate_self_signed, resolve_tls


def make_config(tmp_path, tls: TlsConfig) -> AppConfig:
    return AppConfig(
        interval_minutes=5,
        tls=tls,
        database_path=str(tmp_path / "adguard-sync.db"),
        primary=HostConfig(
            name="primary",
            url="http://primary.local",
            username="admin",
            password="secret",
        ),
        followers=[
            HostConfig(
                name="follower-a",
                url="http://follower-a.local",
                username="admin",
                password="secret",
            )
        ],
    )


def test_resolve_tls_disabled_returns_none(tmp_path):
    config = make_config(tmp_path, TlsConfig(enabled=False))

    assert resolve_tls(config) is None


def test_resolve_tls_self_signed_generates_then_reuses(tmp_path):
    config = make_config(tmp_path, TlsConfig())

    first = resolve_tls(config)
    assert first is not None
    cert_path = tmp_path / "certs" / "selfsigned.crt"
    key_path = tmp_path / "certs" / "selfsigned.key"
    assert cert_path.is_file() and key_path.is_file()
    assert first.cert_file == str(cert_path)
    # Key is owner-only readable/writable.
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    fingerprint = cert_path.read_bytes()

    second = resolve_tls(config)
    assert second == first
    assert cert_path.read_bytes() == fingerprint  # not regenerated


def test_resolve_tls_provided_cert_returns_paths(tmp_path):
    cert_path = tmp_path / "server.crt"
    key_path = tmp_path / "server.key"
    generate_self_signed(cert_path, key_path)
    config = make_config(tmp_path, TlsConfig(cert_file=str(cert_path), key_file=str(key_path)))

    resolved = resolve_tls(config)

    assert resolved is not None
    assert resolved.cert_file == str(cert_path)
    assert resolved.key_file == str(key_path)


def test_resolve_tls_missing_provided_cert_raises(tmp_path):
    config = make_config(
        tmp_path,
        TlsConfig(cert_file=str(tmp_path / "nope.crt"), key_file=str(tmp_path / "nope.key")),
    )

    with pytest.raises(TlsError, match="not found"):
        resolve_tls(config)


def test_generate_self_signed_includes_localhost_san(tmp_path):
    cert_path = tmp_path / "server.crt"
    key_path = tmp_path / "server.key"
    generate_self_signed(cert_path, key_path)

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san.get_values_for_type(x509.DNSName)
