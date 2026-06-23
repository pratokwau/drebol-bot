import os
import sqlite3
from typing import Dict

class Database:
    def __init__(self, db_file):
        self.conn = sqlite3.connect(db_file, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.profit_conns: Dict[int, sqlite3.Connection] = {}
        self.create_tables()

    def create_tables(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS admin_config 
                               (id INTEGER PRIMARY KEY, gk TEXT, ua TEXT)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS orders_data 
                               (order_id TEXT PRIMARY KEY, prime_cost REAL)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS profits 
                               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                user_id INTEGER NOT NULL,
                                type TEXT,
                                buy_price REAL,
                                sell_price REAL,
                                profit REAL,
                                date TEXT)''')
        self.conn.commit()

    def get_config(self):
        self.cursor.execute("SELECT gk, ua FROM admin_config WHERE id = 1")
        res = self.cursor.fetchone()
        return res if res else (None, None)

    def update_config(self, gk=None, ua=None):
        current_gk, current_ua = self.get_config()
        new_gk = gk if gk is not None else current_gk
        new_ua = ua if ua is not None else current_ua
        self.cursor.execute("INSERT OR REPLACE INTO admin_config (id, gk, ua) VALUES (1, ?, ?)", (new_gk, new_ua))
        self.conn.commit()

    def get_prime_cost(self, order_id):
        self.cursor.execute("SELECT prime_cost FROM orders_data WHERE order_id = ?", (order_id,))
        res = self.cursor.fetchone()
        return res[0] if res else None

    def set_prime_cost(self, order_id, cost):
        self.cursor.execute("INSERT OR REPLACE INTO orders_data (order_id, prime_cost) VALUES (?, ?)", (order_id, cost))
        self.conn.commit()

    def _profit_db_path(self, user_id):
        base_dir = os.path.join("saveprofit", str(user_id))
        os.makedirs(base_dir, exist_ok=True)
        return os.path.join(base_dir, "profit.db")

    def _get_profit_conn(self, user_id):
        user_id = int(user_id)
        conn = self.profit_conns.get(user_id)
        if conn is not None:
            return conn

        conn = sqlite3.connect(self._profit_db_path(user_id), check_same_thread=False)
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS profits 
                       (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        type TEXT,
                        buy_price REAL,
                        sell_price REAL,
                        profit REAL,
                        date TEXT)''')
        conn.commit()
        self.profit_conns[user_id] = conn
        return conn

    def load_profits(self, user_id):
        user_id = int(user_id)
        conn = self._get_profit_conn(user_id)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT type, buy_price, sell_price, profit, date FROM profits WHERE user_id = ? ORDER BY id ASC",
            (user_id,)
        )
        rows = cursor.fetchall()
        if rows:
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

        # Миграция старых данных из общей базы в личную базу пользователя
        legacy_rows = []
        try:
            self.cursor.execute(
                "SELECT type, buy_price, sell_price, profit, date FROM profits WHERE user_id = ? ORDER BY id ASC",
                (user_id,)
            )
            legacy_rows = self.cursor.fetchall()
        except Exception:
            legacy_rows = []

        if not legacy_rows:
            return []

        migrated = [
            {
                "type": row[0],
                "buy_price": row[1] or 0,
                "sell_price": row[2] or 0,
                "profit": row[3] or 0,
                "date": row[4] or "",
            }
            for row in legacy_rows
        ]
        self.save_profits(user_id, migrated)
        return migrated

    def save_profits(self, user_id, profits):
        user_id = int(user_id)
        conn = self._get_profit_conn(user_id)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM profits WHERE user_id = ?", (user_id,))
        cursor.executemany(
            "INSERT INTO profits (user_id, type, buy_price, sell_price, profit, date) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    user_id,
                    p.get("type", ""),
                    p.get("buy_price", 0),
                    p.get("sell_price", 0),
                    p.get("profit", 0),
                    p.get("date", ""),
                )
                for p in profits
            ]
        )
        conn.commit()

db = Database("funpay_admin.db")
