import secrets
from datetime import datetime

from pathlib import Path

from base_store import admin_db_path, connect


class AccountDatabase:
    def __init__(self, db_file: str):
        self.db_file = db_file
        self.conn = connect(db_file)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS admin_config 
                               (id INTEGER PRIMARY KEY, gk TEXT, ua TEXT)''')
        self.conn.commit()

    def get_config(self):
        self.cursor.execute("SELECT gk, ua FROM admin_config WHERE id = 1")
        res = self.cursor.fetchone()
        return res if res else (None, None)

    def update_config(self, gk=None, ua=None):
        current_gk, current_ua = self.get_config()
        new_gk = gk if gk is not None else current_gk
        new_ua = ua if ua is not None else current_ua
        self.cursor.execute(
            "INSERT OR REPLACE INTO admin_config (id, gk, ua) VALUES (1, ?, ?)",
            (new_gk, new_ua)
        )
        self.conn.commit()

class OrdersDatabase:
    def __init__(self, db_file: str):
        self.db_file = db_file
        self.conn = connect(db_file)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS orders_data 
                               (order_id TEXT PRIMARY KEY,
                                prime_cost REAL,
                                sell_price REAL,
                                order_date TEXT)''')
        self.conn.commit()
        self._ensure_column("sell_price", "REAL")
        self._ensure_column("order_date", "TEXT")

    def _ensure_column(self, column_name: str, column_type: str):
        self.cursor.execute("PRAGMA table_info(orders_data)")
        columns = {row[1] for row in self.cursor.fetchall()}
        if column_name not in columns:
            self.cursor.execute(f"ALTER TABLE orders_data ADD COLUMN {column_name} {column_type}")
            self.conn.commit()

    def get_prime_cost(self, order_id):
        self.cursor.execute("SELECT prime_cost FROM orders_data WHERE order_id = ?", (order_id,))
        res = self.cursor.fetchone()
        return res[0] if res else None

    def set_prime_cost(self, order_id, cost):
        self.cursor.execute(
            '''INSERT INTO orders_data (order_id, prime_cost) VALUES (?, ?)
               ON CONFLICT(order_id) DO UPDATE SET prime_cost = excluded.prime_cost''',
            (order_id, cost)
        )
        self.conn.commit()

    def get_sell_price(self, order_id):
        self.cursor.execute("SELECT sell_price FROM orders_data WHERE order_id = ?", (order_id,))
        res = self.cursor.fetchone()
        return res[0] if res and res[0] is not None else None

    def set_sell_price(self, order_id, sell_price, order_date=None):
        self.cursor.execute(
            '''INSERT INTO orders_data (order_id, sell_price, order_date) VALUES (?, ?, ?)
               ON CONFLICT(order_id) DO UPDATE SET
                   sell_price = excluded.sell_price,
                   order_date = COALESCE(excluded.order_date, orders_data.order_date)''',
            (order_id, sell_price, order_date),
        )
        self.conn.commit()

    def set_order_date(self, order_id, order_date):
        if not order_date:
            return
        self.cursor.execute(
            '''INSERT INTO orders_data (order_id, order_date) VALUES (?, ?)
               ON CONFLICT(order_id) DO UPDATE SET order_date = excluded.order_date''',
            (order_id, order_date),
        )
        self.conn.commit()

    def list_prime_costs(self, limit: int = 500):
        self.cursor.execute(
            "SELECT order_id, prime_cost FROM orders_data ORDER BY rowid DESC LIMIT ?",
            (int(limit),),
        )
        return self.cursor.fetchall()

    def delete_prime_cost(self, order_id):
        self.cursor.execute("DELETE FROM orders_data WHERE order_id = ?", (order_id,))
        self.conn.commit()


class ProfitDatabase:
    def __init__(self, user_id: int, db_file: str | None = None):
        self.user_id = int(user_id or 0)
        self.db_file = db_file or admin_db_path("ordersfp")
        self.conn = connect(self.db_file)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS profits 
                               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                user_id INTEGER NOT NULL,
                                type TEXT,
                                buy_price REAL,
                                sell_price REAL,
                                profit REAL,
                                date TEXT)''')
        self.conn.commit()

    def load_profits(self):
        self.create_tables()
        self.cursor.execute(
            "SELECT type, buy_price, sell_price, profit, date FROM profits WHERE user_id = ? ORDER BY id ASC",
            (self.user_id,)
        )
        rows = self.cursor.fetchall()
        return [
            {
                "type": row[0],
                "buy_price": row[1] or 0,
                "sell_price": row[2] or 0,
                "profit": row[3] or 0,
                "date": row[4] or "",
            }
            for row in rows
        ]

    def save_profits(self, profits):
        self.create_tables()
        if profits is None:
            profits = []
        self.cursor.execute("DELETE FROM profits WHERE user_id = ?", (self.user_id,))
        self.cursor.executemany(
            "INSERT INTO profits (user_id, type, buy_price, sell_price, profit, date) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    self.user_id,
                    p.get("type", ""),
                    p.get("buy_price", 0),
                    p.get("sell_price", 0),
                    p.get("profit", 0),
                    p.get("date", ""),
                )
                for p in profits
            ]
        )
        self.conn.commit()


class WebSessionDatabase:
    def __init__(self, db_file: str):
        self.db_file = db_file
        self.conn = connect(db_file)
        self.cursor = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cursor.execute(
            '''CREATE TABLE IF NOT EXISTS web_sessions
               (session_id TEXT PRIMARY KEY,
                username TEXT,
                created_at TEXT,
                last_seen TEXT,
                revoked INTEGER DEFAULT 0)'''
        )
        self.conn.commit()

    def create_session(self, username: str) -> str:
        session_id = secrets.token_urlsafe(32)
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        self.cursor.execute(
            "INSERT INTO web_sessions (session_id, username, created_at, last_seen, revoked) VALUES (?, ?, ?, ?, 0)",
            (session_id, username, now, now),
        )
        self.conn.commit()
        return session_id

    def get_session(self, session_id: str):
        self.cursor.execute(
            "SELECT session_id, username, created_at, last_seen, revoked FROM web_sessions WHERE session_id = ? AND revoked = 0",
            (session_id,),
        )
        return self.cursor.fetchone()

    def touch_session(self, session_id: str):
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        self.cursor.execute(
            "UPDATE web_sessions SET last_seen = ? WHERE session_id = ?",
            (now, session_id),
        )
        self.conn.commit()

    def list_sessions(self):
        self.cursor.execute(
            "SELECT session_id, username, created_at, last_seen, revoked FROM web_sessions ORDER BY created_at DESC"
        )
        return self.cursor.fetchall()

    def revoke_session(self, session_id: str):
        self.cursor.execute(
            "UPDATE web_sessions SET revoked = 1 WHERE session_id = ?",
            (session_id,),
        )
        self.conn.commit()

    def delete_session(self, session_id: str):
        self.cursor.execute(
            "DELETE FROM web_sessions WHERE session_id = ?",
            (session_id,),
        )
        self.conn.commit()


db = AccountDatabase(admin_db_path("funpayacc"))
orders_db = OrdersDatabase(admin_db_path("ordersfp"))
web_db = WebSessionDatabase(admin_db_path("webauth"))


def _table_exists(conn, table_name: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    )
    return cur.fetchone() is not None


def migrate_legacy_funpay_storage():
    legacy_main = Path(admin_db_path("main"))
    if legacy_main.exists():
        legacy_conn = connect(str(legacy_main))
        legacy_cur = legacy_conn.cursor()

        if _table_exists(legacy_conn, "admin_config") and db.get_config() == (None, None):
            legacy_cur.execute("SELECT gk, ua FROM admin_config WHERE id = 1")
            row = legacy_cur.fetchone()
            if row:
                db.update_config(gk=row[0], ua=row[1])

        if _table_exists(legacy_conn, "orders_data"):
            legacy_cur.execute("SELECT order_id, prime_cost FROM orders_data")
            for order_id, prime_cost in legacy_cur.fetchall():
                if orders_db.get_prime_cost(order_id) is None:
                    orders_db.set_prime_cost(order_id, prime_cost)

    data_dir = Path("data")
    if not data_dir.exists():
        return

    for legacy_db in data_dir.glob("*_saveprofit.db"):
        user_prefix = legacy_db.name.split("_", 1)[0]
        if not user_prefix.isdigit():
            continue
        user_id = int(user_prefix)
        profit_db = ProfitDatabase(user_id)
        if profit_db.load_profits():
            continue

        legacy_conn = connect(str(legacy_db))
        if not _table_exists(legacy_conn, "profits"):
            continue
        legacy_cur = legacy_conn.cursor()
        legacy_cur.execute(
            "SELECT type, buy_price, sell_price, profit, date FROM profits WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        )
        rows = legacy_cur.fetchall()
        if rows:
            profit_db.save_profits([
                {
                    "type": row[0],
                    "buy_price": row[1] or 0,
                    "sell_price": row[2] or 0,
                    "profit": row[3] or 0,
                    "date": row[4] or "",
                }
                for row in rows
            ])


migrate_legacy_funpay_storage()
