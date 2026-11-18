"""Portfolio, cache and settings are owner-only (audit CR2)."""
from __future__ import annotations

import os
import stat

from mazecrypto.core import settings as settings_mod
from mazecrypto.core.store import ProfileStore


def _mode(p) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def test_new_profile_db_is_private(tmp_path):
    old = os.umask(0o022)
    try:
        db = tmp_path / "maze-crypto" / "profiles" / "default.db"
        s = ProfileStore(db)
        s.con.execute("CREATE TABLE IF NOT EXISTS x (a)")
        s.con.commit()
        s.close()
    finally:
        os.umask(old)
    assert _mode(db) == 0o600
    assert _mode(db.parent) == 0o700
    assert _mode(db.parent.parent) == 0o700
    for side in ("-wal", "-shm"):
        f = db.with_name(db.name + side)
        if f.exists():
            assert _mode(f) == 0o600, side


def test_existing_world_readable_db_is_tightened(tmp_path):
    d = tmp_path / "maze-crypto" / "profiles"
    d.mkdir(parents=True, mode=0o755)
    db = d / "default.db"
    db.write_bytes(b"")
    db.chmod(0o644)
    ProfileStore(db).close()
    assert _mode(db) == 0o600
    assert _mode(d) == 0o700


def test_settings_file_is_private(tmp_path):
    p = tmp_path / "cfg" / "config.toml"
    old = os.umask(0o022)
    try:
        settings_mod.save(settings_mod.Settings(), p)
    finally:
        os.umask(old)
    assert _mode(p) == 0o600
    assert _mode(p.parent) == 0o700
    p.chmod(0o644)
    settings_mod.load(p)
    assert _mode(p) == 0o600
