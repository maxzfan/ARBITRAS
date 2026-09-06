"""The map is a credential-class input (TRACK_E.md): unsigned or tampered
maps are not consulted. Library crypto only — Ed25519 from `cryptography`."""
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from backend.terrain.rastermap import RasterMap
from backend.terrain.signing import (generate_keypair, load_verified, sign_file,
                                     verify_file)

FIXTURE = Path("fixtures/terrain_fixture.json")


def test_sign_verify_roundtrip_and_tamper(tmp_path):
    priv, pub = tmp_path / "k.pem", tmp_path / "k.pub"
    generate_keypair(priv, pub)
    m = RasterMap.from_fixture(FIXTURE)
    p = m.save_npz(tmp_path / "map.npz")
    sig = sign_file(p, priv)
    assert sig.exists() and sig.stat().st_size == 64
    ok, why = verify_file(p, pub)
    assert ok, why
    loaded = load_verified(p, pub)
    assert loaded.signed is True and loaded.checksum() == m.checksum()
    # tamper one cell, re-save under the same name: signature no longer holds
    m.grid[0, 0] = 6
    m.save_npz(p)
    ok, why = verify_file(p, pub)
    assert not ok and "signature" in why
    with pytest.raises(ValueError):
        load_verified(p, pub)


def test_missing_signature_is_a_named_reason(tmp_path):
    priv, pub = tmp_path / "k.pem", tmp_path / "k.pub"
    generate_keypair(priv, pub)
    p = RasterMap.from_fixture(FIXTURE).save_npz(tmp_path / "map.npz")
    ok, why = verify_file(p, pub)
    assert not ok and "no signature" in why
