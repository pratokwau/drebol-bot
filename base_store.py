import os
import sqlite3
import shutil
from pathlib import Path
from typing import Dict


BASE_DIR = Path("data")
_CONNS: Dict[str, sqlite3.Connection] = {}


def ensure_dir(path: Path | str) -> str:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def admin_dir() -> str:
    return ensure_dir(BASE_DIR)


def user_dir(user_id: int) -> str:
    return ensure_dir(BASE_DIR)


def admin_db_path(name: str) -> str:
    return str(Path(admin_dir()) / f"{name}.db")


def user_db_path(user_id: int, name: str) -> str:
    return str(Path(user_dir(user_id)) / f"{int(user_id)}_{name}.db")


def connect(db_path: str) -> sqlite3.Connection:
    db_path = str(Path(db_path))
    conn = _CONNS.get(db_path)
    if conn is not None:
        return conn
    ensure_dir(Path(db_path).parent)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    _CONNS[db_path] = conn
    return conn


def _move_file(src: Path, dst: Path):
    if not src.exists() or dst.exists():
        return
    ensure_dir(dst.parent)
    shutil.move(str(src), str(dst))


def migrate_legacy_storage():
    ensure_dir(BASE_DIR)

    legacy_authorized = Path("authorized.json")
    _move_file(legacy_authorized, BASE_DIR / "authorized.json")

    legacy_base = Path("base")
    if legacy_base.exists():
        for db_file in legacy_base.glob("*/*.db"):
            owner_dir = db_file.parent.name
            if owner_dir == "admin":
                dst = BASE_DIR / db_file.name
            else:
                dst = BASE_DIR / f"{owner_dir}_{db_file.stem}.db"
            _move_file(db_file, dst)

    legacy_users = Path("users")
    if legacy_users.exists():
        for user_dir_path in legacy_users.iterdir():
            if not user_dir_path.is_dir() or not user_dir_path.name.isdigit():
                continue
            user_id = user_dir_path.name
            for json_file in user_dir_path.glob("*.json"):
                if json_file.name in {"ai_chats.json", "certificates.json", "minprice.json"}:
                    _move_file(json_file, BASE_DIR / json_file.name)


migrate_legacy_storage()
