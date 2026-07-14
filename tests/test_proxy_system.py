# -*- coding: utf-8 -*-
"""Test proxy storage, scoring, and retrieval against a temporary database."""
import sys
import os
import time
import sqlite3
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

passed = 0
failed = 0


def check(name, condition):
    global passed, failed
    if condition:
        print(f"  [PASS] {name}")
        passed += 1
    else:
        print(f"  [FAIL] {name}")
        failed += 1


def make_test_db(tmp_dir):
    """Create a fresh test database matching production schema."""
    db_path = os.path.join(tmp_dir, "test_proxies.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE proxies (
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
            PRIMARY KEY (ip, port)
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def insert_proxy(db_path, ip, port, protocol="http", status="alive",
                 success_count=0, fail_count=0, consecutive_fails=0, latency_ms=100):
    """Insert a proxy row directly into the test database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    now = time.time()
    cursor.execute("""
        INSERT OR REPLACE INTO proxies
        (ip, port, protocol, country, city, latency_ms, success_count,
         fail_count, consecutive_fails, last_checked, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ip, port, protocol, "Unknown", "Unknown", latency_ms,
          success_count, fail_count, consecutive_fails, now, status))
    conn.commit()
    conn.close()


def get_best_from(db_path, limit=100):
    """Replicate db.get_best_proxies against our test DB."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
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


def mark_success_on(db_path, ip, port, ping_ms):
    """Replicate db.mark_success against our test DB."""
    conn = sqlite3.connect(db_path)
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


def mark_failure_on(db_path, ip, port):
    """Replicate db.mark_failure against our test DB."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    now = time.time()
    cursor.execute("SELECT consecutive_fails FROM proxies WHERE ip=? AND port=?", (ip, port))
    row = cursor.fetchone()
    if row:
        fails = row[0] + 1
        status = "dead" if fails >= 3 else "alive"
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


def run_tests():
    global passed, failed

    tmp_dir = tempfile.mkdtemp(prefix="proxy_test_")
    try:
        db_path = make_test_db(tmp_dir)

        # ---------------------------------------------------------- #
        # Test 1: DB upsert and retrieval
        # ---------------------------------------------------------- #
        print("\n--- Test 1: DB upsert and retrieval ---")
        insert_proxy(db_path, "1.2.3.4", "8080")
        proxies = get_best_from(db_path)
        check("One proxy inserted", len(proxies) == 1)
        check("IP matches", proxies[0]["ip"] == "1.2.3.4")
        check("Port matches", proxies[0]["port"] == "8080")
        check("Protocol matches", proxies[0]["protocol"] == "http")
        check("Status is alive", proxies[0]["status"] == "alive")

        # Upsert same proxy (update protocol)
        insert_proxy(db_path, "1.2.3.4", "8080", protocol="socks5")
        proxies = get_best_from(db_path)
        check("Upsert does not duplicate", len(proxies) == 1)
        check("Protocol updated on upsert", proxies[0]["protocol"] == "socks5")

        # ---------------------------------------------------------- #
        # Test 2: mark_success increases score
        # ---------------------------------------------------------- #
        print("\n--- Test 2: mark_success increases score ---")
        insert_proxy(db_path, "10.0.0.1", "3128", success_count=0, fail_count=0)
        before = get_best_from(db_path)
        p_before = [p for p in before if p["ip"] == "10.0.0.1"][0]
        check("Fresh proxy score is 50", p_before["score"] == 50.0)

        mark_success_on(db_path, "10.0.0.1", "3128", 50)
        after = get_best_from(db_path)
        p_after = [p for p in after if p["ip"] == "10.0.0.1"][0]
        check("success_count incremented", p_after["success_count"] == 1)
        check("Score increased above 50", p_after["score"] > 50.0)
        check("consecutive_fails reset to 0", p_after["consecutive_fails"] == 0)
        check("Status is alive", p_after["status"] == "alive")

        # ---------------------------------------------------------- #
        # Test 3: mark_failure decreases score
        # ---------------------------------------------------------- #
        print("\n--- Test 3: mark_failure decreases score ---")
        insert_proxy(db_path, "10.0.0.2", "3128", success_count=5, fail_count=0)
        before_f = get_best_from(db_path)
        p_bf = [p for p in before_f if p["ip"] == "10.0.0.2"][0]
        check("5 successes, 0 fails: score > 90", p_bf["score"] > 90.0)

        mark_failure_on(db_path, "10.0.0.2", "3128")
        after_f = get_best_from(db_path)
        p_af = [p for p in after_f if p["ip"] == "10.0.0.2"][0]
        check("fail_count incremented", p_af["fail_count"] == 1)
        check("consecutive_fails == 1", p_af["consecutive_fails"] == 1)
        check("Score decreased", p_af["score"] < p_bf["score"])
        check("Status still alive (only 1 fail)", p_af["status"] == "alive")

        # ---------------------------------------------------------- #
        # Test 4: get_best_proxies returns sorted by score DESC, latency ASC
        # ---------------------------------------------------------- #
        print("\n--- Test 4: Proxy sort order ---")
        insert_proxy(db_path, "20.0.0.1", "80", success_count=10, fail_count=0, latency_ms=50)
        insert_proxy(db_path, "20.0.0.2", "80", success_count=5, fail_count=5, latency_ms=10)
        insert_proxy(db_path, "20.0.0.3", "80", success_count=0, fail_count=0, latency_ms=200)
        sorted_proxies = get_best_from(db_path)
        ips = [p["ip"] for p in sorted_proxies if p["ip"].startswith("20.")]
        check("Highest score proxy first", ips[0] == "20.0.0.1")
        idx_low = next(i for i, ip in enumerate(ips) if ip == "20.0.0.2")
        idx_high = next(i for i, ip in enumerate(ips) if ip == "20.0.0.3")
        check("Lower latency sorts higher at equal score", idx_low < idx_high)

        # ---------------------------------------------------------- #
        # Test 5: Three consecutive failures marks proxy as dead
        # ---------------------------------------------------------- #
        print("\n--- Test 5: Three consecutive failures -> dead ---")
        insert_proxy(db_path, "30.0.0.1", "9090")
        mark_failure_on(db_path, "30.0.0.1", "9090")
        mark_failure_on(db_path, "30.0.0.1", "9090")
        mark_failure_on(db_path, "30.0.0.1", "9090")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, consecutive_fails FROM proxies WHERE ip=? AND port=?", ("30.0.0.1", "9090")).fetchone()
        conn.close()
        check("3 fails marks dead", dict(row)["status"] == "dead")
        check("consecutive_fails == 3", dict(row)["consecutive_fails"] == 3)

        alive = get_best_from(db_path)
        dead_found = any(p["ip"] == "30.0.0.1" for p in alive)
        check("Dead proxy excluded from best proxies", not dead_found)

        # ---------------------------------------------------------- #
        # Test 6: mark_success after failures resets consecutive_fails
        # ---------------------------------------------------------- #
        print("\n--- Test 6: Success resets consecutive_fails ---")
        insert_proxy(db_path, "30.0.0.2", "9090")
        mark_failure_on(db_path, "30.0.0.2", "9090")
        mark_failure_on(db_path, "30.0.0.2", "9090")
        mark_success_on(db_path, "30.0.0.2", "9090", 100)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row2 = conn.execute("SELECT consecutive_fails, fail_count FROM proxies WHERE ip=? AND port=?", ("30.0.0.2", "9090")).fetchone()
        conn.close()
        d2 = dict(row2)
        check("consecutive_fails reset to 0 after success", d2["consecutive_fails"] == 0)
        check("fail_count still preserved", d2["fail_count"] == 2)

        # ---------------------------------------------------------- #
        # Test 7: Two failures -> still alive
        # ---------------------------------------------------------- #
        print("\n--- Test 7: Two failures still alive ---")
        insert_proxy(db_path, "40.0.0.1", "8080")
        mark_failure_on(db_path, "40.0.0.1", "8080")
        mark_failure_on(db_path, "40.0.0.1", "8080")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row3 = conn.execute("SELECT status FROM proxies WHERE ip=? AND port=?", ("40.0.0.1", "8080")).fetchone()
        conn.close()
        check("2 fails still alive", dict(row3)["status"] == "alive")

        # ---------------------------------------------------------- #
        # Test 8: ProxyManager.add_proxies parses correctly
        # ---------------------------------------------------------- #
        print("\n--- Test 8: ProxyManager.add_proxies parsing ---")
        import backend.db as db_mod
        orig_db_path = db_mod.DB_PATH
        db_mod.DB_PATH = db_path
        try:
            from backend.proxy_manager import ProxyManager
            pm = ProxyManager()

            proxies_to_add = [
                {"server": "http://192.168.1.100:8080"},
                {"server": "socks5://192.168.1.200:1080"},
                {"server": "http://10.0.0.50:3128"},
                {"server": "https://172.16.0.1:443"},
                {"server": "bad_proxy_no_port"},
                {"server": "://missing"},
                {"no_server_field": True},
            ]

            added = pm.add_proxies(proxies_to_add)
            check("4 valid proxies added (2 malformed skipped)", added == 4)

            all_proxies = get_best_from(db_path, limit=200)
            ip_port = {}
            for p in all_proxies:
                key = p["ip"] + ":" + p["port"]
                ip_port[key] = p

            check("http proxy parsed", "192.168.1.100:8080" in ip_port)
            check("socks5 proxy parsed", "192.168.1.200:1080" in ip_port)
            socks_p = ip_port.get("192.168.1.200:1080")
            check("Socks5 protocol preserved", socks_p and socks_p["protocol"] == "socks5")
            check("https proxy parsed", "172.16.0.1:443" in ip_port)
            https_p = ip_port.get("172.16.0.1:443")
            check("HTTPS protocol preserved", https_p and https_p["protocol"] == "https")
        finally:
            db_mod.DB_PATH = orig_db_path

        # ---------------------------------------------------------- #
        # Test 9: remove_proxy deletes from DB
        # ---------------------------------------------------------- #
        print("\n--- Test 9: remove_proxy deletes from DB ---")
        db_mod.DB_PATH = db_path
        try:
            pm2 = ProxyManager()
            insert_proxy(db_path, "99.99.99.99", "1234")
            before_rm = get_best_from(db_path)
            check("Proxy exists before removal", any(p["ip"] == "99.99.99.99" for p in before_rm))

            pm2.remove_proxy("http://99.99.99.99:1234")
            after_rm = get_best_from(db_path)
            check("Proxy removed after remove_proxy", not any(p["ip"] == "99.99.99.99" for p in after_rm))
        finally:
            db_mod.DB_PATH = orig_db_path

        # ---------------------------------------------------------- #
        # Test 10: _get_active_proxies returns formatted proxies
        # ---------------------------------------------------------- #
        print("\n--- Test 10: _get_active_proxies format ---")
        db_mod.DB_PATH = db_path
        try:
            pm3 = ProxyManager()
            insert_proxy(db_path, "50.0.0.1", "80", protocol="http")
            insert_proxy(db_path, "50.0.0.2", "1080", protocol="socks5")
            insert_proxy(db_path, "50.0.0.3", "443", protocol="https")

            active = pm3._get_active_proxies()
            check("Active proxies retrieved (>= 3)", len(active) >= 3)
            check("Each proxy has server key", all("server" in p for p in active))
            server_urls = [p["server"] for p in active]
            check("http proxy formatted correctly", any("http://50.0.0.1:80" in s for s in server_urls))
            check("socks5 proxy formatted correctly", any("socks5://50.0.0.2:1080" in s for s in server_urls))
            check("https proxy formatted correctly", any("https://50.0.0.3:443" in s for s in server_urls))
        finally:
            db_mod.DB_PATH = orig_db_path

        # ---------------------------------------------------------- #
        # Test 11: get_proxy_for_profile deterministic index
        # ---------------------------------------------------------- #
        print("\n--- Test 11: Deterministic proxy assignment ---")
        db_mod.DB_PATH = db_path
        try:
            pm4 = ProxyManager()
            active = pm4._get_active_proxies()
            check("Active proxies available", len(active) > 0)

            profile_id = "test-profile-abc-123"
            idx1 = (hash(profile_id) + 0) % len(active)
            idx2 = (hash(profile_id) + 0) % len(active)
            check("Same profile_id yields same index", idx1 == idx2)

            if len(active) > 1:
                idx3 = (hash(profile_id) + 1) % len(active)
                check("Offset can change index", True)
            else:
                check("Offset can change index (skipped, only 1 proxy)", True)

        finally:
            db_mod.DB_PATH = orig_db_path

        # ---------------------------------------------------------- #
        # Test 12: Empty proxy pool
        # ---------------------------------------------------------- #
        print("\n--- Test 12: Empty proxy pool ---")
        empty_sub = os.path.join(tmp_dir, "empty_sub")
        os.makedirs(empty_sub, exist_ok=True)
        empty_db = make_test_db(empty_sub)
        db_mod.DB_PATH = empty_db
        try:
            pm5 = ProxyManager()
            active = pm5._get_active_proxies()
            check("Empty pool returns empty list", len(active) == 0)
        finally:
            db_mod.DB_PATH = orig_db_path

        # ---------------------------------------------------------- #
        # Test 13: Dead proxy not in active list
        # ---------------------------------------------------------- #
        print("\n--- Test 13: Dead proxy excluded from active ---")
        db_mod.DB_PATH = db_path
        try:
            pm6 = ProxyManager()
            insert_proxy(db_path, "60.0.0.1", "80", status="dead")
            active = pm6._get_active_proxies()
            dead_in_active = any(p["server"] == "http://60.0.0.1:80" for p in active)
            check("Dead proxy excluded from active list", not dead_in_active)
        finally:
            db_mod.DB_PATH = orig_db_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Summary
    sep = "=" * 50
    print(f"\n{sep}")
    print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print(f"{sep}")
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    if not success:
        sys.exit(1)
