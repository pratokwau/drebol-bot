import os
import sqlite3
from pathlib import Path
from typing import Dict


BASE_DIR = Path("base")
_CONNS: Dict[str, sqlite3.Connection] = {}


def ensure_dir(path: Path | str) -> str:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def admin_dir() -> str:
    return ensure_dir(BASE_DIR / "admin")


def user_dir(user_id: int) -> str:
    return ensure_dir(BASE_DIR / str(int(user_id)))


def admin_db_path(name: str) -> str:
    return str(Path(admin_dir()) / f"{name}.db")


def user_db_path(user_id: int, name: str) -> str:
    return str(Path(user_dir(user_id)) / f"{name}.db")


def connect(db_path: str) -> sqlite3.Connection:
    db_path = str(Path(db_path))
    conn = _CONNS.get(db_path)
    if conn is not None:
        return conn
    ensure_dir(Path(db_path).parent)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    _CONNS[db_path] = conn
    return conn
