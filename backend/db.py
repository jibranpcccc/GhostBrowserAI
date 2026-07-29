import sqlite3
import os
import time
import json
import stat
from typing import List, Dict
from cryptography.fernet import Fernet, InvalidToken

from backend.config import get_data_dir

DB_PATH = get_data_dir("backend", "proxies.db")
PROXY_KEY_PATH = get_data_dir("backend", ".proxy_credentials.key")


def _get_proxy_cipher() -> Fernet:
    """Return the local at-rest cipher used only for proxy authentication data."""
    key_dir = os.path.dirname(PROXY_KEY_PATH)
    os.makedirs(key_dir, exist_ok=True)
    if not os.path.exists(PROXY_KEY_PATH):
        key = Fernet.generate_key()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(PROXY_KEY_PATH, flags, stat.S_IRUSR | stat.S_IWUSR)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
    else:
        with open(PROXY_KEY_PATH, "rb") as handle:
            key = handle.read()
    try:
        os.chmod(PROXY_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return Fernet(key)


def _encrypt_proxy_credentials(username: str = "", password: str = "") -> str:
    if not username and not password:
        return ""
    payload = json.dumps(
        {"username": username or "", "password": password or ""},
        separators=(",", ":"),
    ).encode("utf-8")
    return "enc:" + _get_proxy_cipher().encrypt(payload).decode("ascii")


def _decrypt_proxy_credentials(value: str) -> Dict[str, str]:
    if not value:
        return {"username": "", "password": ""}
    if not isinstance(value, str) or not value.startswith("enc:"):
        # Plaintext credentials are never accepted back into runtime.
        return {"username": "", "password": ""}
    try:
        raw = _get_proxy_cipher().decrypt(value[4:].encode("ascii"))
        decoded = json.loads(raw.decode("utf-8"))
        return {
            "username": str(decoded.get("username") or ""),
            "password": str(decoded.get("password") or ""),
        }
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        return {"username": "", "password": ""}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS proxies (
            ip TEXT,
            port TEXT,
            protocol TEXT,
            country TEXT,
            city TEXT,
            latency_ms INTEGER,
            success_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0,
            consecutive_fails INTEGER DEFAULT 0,
            last_checked REAL,
            status TEXT,
            credentials_enc TEXT DEFAULT '',
            PRIMARY KEY (ip, port)
        )
    """)
    cursor.execute("PRAGMA table_info(proxies)")
    columns = {row[1] for row in cursor.fetchall()}
    if "credentials_enc" not in columns:
        cursor.execute("ALTER TABLE proxies ADD COLUMN credentials_enc TEXT DEFAULT ''")

    # Backward-compatible migration for any earlier experimental schema that
    # stored username/password columns in plaintext. Values are encrypted first,
    # then the legacy cells are wiped. Existing production schema did not have
    # these columns, so this is normally a no-op.
    if "username" in columns or "password" in columns:
        username_expr = "username" if "username" in columns else "''"
        password_expr = "password" if "password" in columns else "''"
        cursor.execute(
            f"SELECT rowid, {username_expr}, {password_expr} FROM proxies "
            "WHERE COALESCE(credentials_enc, '') = ''"
        )
        for rowid, username, password in cursor.fetchall():
            encrypted = _encrypt_proxy_credentials(username or "", password or "")
            if encrypted:
                cursor.execute(
                    "UPDATE proxies SET credentials_enc=? WHERE rowid=?",
                    (encrypted, rowid),
                )
        assignments = []
        if "username" in columns:
            assignments.append("username=NULL")
        if "password" in columns:
            assignments.append("password=NULL")
        if assignments:
            cursor.execute(f"UPDATE proxies SET {', '.join(assignments)}")
    conn.commit()
    conn.close()

def upsert_proxy(proxy_data: Dict):
    """Insert a new proxy or update an existing one, preserving success/fail stats."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if exists
    cursor.execute("SELECT success_count, fail_count, consecutive_fails FROM proxies WHERE ip=? AND port=?",
                   (proxy_data['ip'], proxy_data['port']))
    row = cursor.fetchone()

    now = time.time()

    if row:
        # Update existing
        credentials_enc = _encrypt_proxy_credentials(
            proxy_data.get("username", ""), proxy_data.get("password", "")
        )
        cursor.execute("""
            UPDATE proxies
            SET protocol=?, country=?, city=?, latency_ms=?, last_checked=?, status=?,
                credentials_enc=CASE WHEN ? != '' THEN ? ELSE credentials_enc END
            WHERE ip=? AND port=?
        """, (proxy_data['protocol'], proxy_data['country'], proxy_data['city'],
              proxy_data['latency_ms'], now, proxy_data['status'], credentials_enc,
              credentials_enc, proxy_data['ip'], proxy_data['port']))
    else:
        # Insert new
        credentials_enc = _encrypt_proxy_credentials(
            proxy_data.get("username", ""), proxy_data.get("password", "")
        )
        cursor.execute("""
            INSERT INTO proxies (
                ip, port, protocol, country, city, latency_ms, last_checked,
                status, credentials_enc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (proxy_data['ip'], proxy_data['port'], proxy_data['protocol'], proxy_data['country'],
              proxy_data['city'], proxy_data['latency_ms'], now, proxy_data['status'],
              credentials_enc))

    conn.commit()
    conn.close()
def mark_failure(ip: str, port: str):
    """Mark a proxy as failed. If consecutive fails >= 3, mark as dead."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = time.time()

    cursor.execute("SELECT consecutive_fails FROM proxies WHERE ip=? AND port=?", (ip, port))
    row = cursor.fetchone()
    if row:
        fails = row[0] + 1
        status = 'dead' if fails >= 3 else 'alive'
        cursor.execute("""
            UPDATE proxies
            SET fail_count = fail_count + 1,
                consecutive_fails = ?,
                last_checked = ?,
                status = ?
            WHERE ip=? AND port=?
        """, (fails, now, status, ip, port))
    conn.commit()
    conn.close()

def get_best_proxies(limit: int = 100) -> List[Dict]:
    """Retrieve the best working proxies sorted by score and latency."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # We select alive proxies and calculate a virtual score
    # Score = (success_count / total_attempts) * 100
    # We penalize proxies with 0 total attempts slightly to prefer proven ones,
    # but still use them if we lack proven ones.

    cursor.execute("""
        SELECT *,
               CASE WHEN (success_count + fail_count) > 0
                    THEN (CAST(success_count AS FLOAT) / (success_count + fail_count)) * 100
                    ELSE 50 END as score
        FROM proxies
        WHERE status = 'alive'
        ORDER BY score DESC, latency_ms ASC
        LIMIT ?
    """, (limit,))

    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        item = dict(row)
        credentials = _decrypt_proxy_credentials(item.pop("credentials_enc", ""))
        item.update(credentials)
        result.append(item)
    return result

# Initialize DB on import
init_db()
