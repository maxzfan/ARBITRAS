"""Ed25519 signature over the map file (TRACK_E.md "The pre-map").

The map is loaded at mission issuance and signed with the same key class
that signs the TESLA anchor: one asymmetric operation, library crypto only
(CLAUDE.md). A map that does not verify is not consulted. On the replay the
same machine generates and verifies the key — a stand-in for the mission
issuer, documented as such in the stream provenance, exactly like the
scripted credential schedule.
"""
from __future__ import annotations

from pathlib import Path

from backend.terrain.rastermap import RasterMap


def _ed25519():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as e:                      # pragma: no cover
        raise ImportError("map signing needs `cryptography` (bash bootstrap.sh)") from e
    return serialization, ed25519


def generate_keypair(priv_path, pub_path) -> tuple[Path, Path]:
    serialization, ed25519 = _ed25519()
    key = ed25519.Ed25519PrivateKey.generate()
    priv_path, pub_path = Path(priv_path), Path(pub_path)
    priv_path.parent.mkdir(parents=True, exist_ok=True)
    priv_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    pub_path.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return priv_path, pub_path


def sig_path_for(path) -> Path:
    path = Path(path)
    return path.with_name(path.name + ".sig")


def sign_file(path, priv_path) -> Path:
    serialization, _ = _ed25519()
    key = serialization.load_pem_private_key(Path(priv_path).read_bytes(), password=None)
    sig = key.sign(Path(path).read_bytes())
    out = sig_path_for(path)
    out.write_bytes(sig)
    return out


def verify_file(path, pub_path, sig_path=None) -> tuple[bool, str]:
    serialization, _ = _ed25519()
    from cryptography.exceptions import InvalidSignature
    path = Path(path)
    sig_path = Path(sig_path) if sig_path else sig_path_for(path)
    if not sig_path.exists():
        return False, f"no signature file at {sig_path}"
    if not Path(pub_path).exists():
        return False, f"no public key at {pub_path}"
    pub = serialization.load_pem_public_key(Path(pub_path).read_bytes())
    try:
        pub.verify(sig_path.read_bytes(), path.read_bytes())
    except InvalidSignature:
        return False, "signature does not verify against the map bytes"
    return True, "verified"


def load_verified(path, pub_path, sig_path=None) -> RasterMap:
    ok, why = verify_file(path, pub_path, sig_path)
    if not ok:
        raise ValueError(f"map not consulted: {why}")
    m = RasterMap.load_npz(path)
    m.signed = True
    return m
