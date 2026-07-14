import sqlite3
import os
import time
from typing import List, Dict

from backend.config import get_data_dir

DB_PATH = get_data_dir("backend", "proxies.db")

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
            timezone TEXT DEFAULT 'UTC',
            locale TEXT DEFAULT 'en-US',
            PRIMARY KEY (ip, port)
        )
    """)
    # Migration: add timezone/locale columns if missing
    try:
        cursor.execute("ALTER TABLE proxies ADD COLUMN timezone TEXT DEFAULT 'UTC'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE proxies ADD COLUMN locale TEXT DEFAULT 'en-US'")
    except sqlite3.OperationalError:
        pass
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
    tz = proxy_data.get('timezone', 'UTC')
    locale = proxy_data.get('locale', 'en-US')
    
    if row:
        # Update existing
        cursor.execute("""
            UPDATE proxies 
            SET protocol=?, country=?, city=?, latency_ms=?, last_checked=?, status=?, timezone=?, locale=?
            WHERE ip=? AND port=?
        """, (proxy_data['protocol'], proxy_data['country'], proxy_data['city'], 
              proxy_data['latency_ms'], now, proxy_data['status'], tz, locale,
              proxy_data['ip'], proxy_data['port']))
    else:
        # Insert new
        cursor.execute("""
            INSERT INTO proxies (ip, port, protocol, country, city, latency_ms, last_checked, status, timezone, locale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (proxy_data['ip'], proxy_data['port'], proxy_data['protocol'], proxy_data['country'], 
              proxy_data['city'], proxy_data['latency_ms'], now, proxy_data['status'], tz, locale))
        
    conn.commit()
    conn.close()

def mark_success(ip: str, port: str, ping_ms: int):
    """Mark a proxy as successful and reset consecutive failures."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = time.time()
    cursor.execute("""
        UPDATE proxies 
        SET success_count = success_count + 1,
            consecutive_fails = 0,
            latency_ms = ?,
            last_checked = ?,
            status = 'alive'
        WHERE ip=? AND port=?
    """, (ping_ms, now, ip, port))
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
    
    return [dict(row) for row in rows]

def get_all_alive_proxies() -> List[Dict]:
    """Retrieve all proxies currently marked alive for background health checks."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM proxies WHERE status = 'alive'")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Initialize DB on import
init_db()
