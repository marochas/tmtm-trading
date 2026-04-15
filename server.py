#!/usr/bin/env python3
"""
TMTM Trading Platform v2
Fázy: 1 (Entry/SL/TP + obrázky), 2 (autentifikácia + admin), 3 (stav + štatistiky)
Spustenie: python3 server.py
Požaduje: Python 3.8+ (bez externých knižníc)
"""

import http.server
import sqlite3
import json
import os
import re
import hashlib
import secrets
import urllib.parse
from datetime import date, datetime
from pathlib import Path

PORT = int(os.environ.get("PORT", 3000))

# DB_PATH: lokálna SQLite (fallback ak Turso nie je nastavený)
_default_db = str(Path(__file__).parent / "trading.db")
DB_PATH = Path(os.environ.get("DB_PATH", _default_db))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Turso cloud SQLite (persistentné úložisko)
TURSO_URL   = os.environ.get("TURSO_URL")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN")

PUBLIC_DIR = Path(__file__).parent / "public"

# ─── DATABASE ─────────────────────────────────────────────────────────────────

class _Row:
    """sqlite3.Row-kompatibilný typ pre libsql_experimental aj sqlite3."""
    __slots__ = ('_data', '_keys')
    def __init__(self, cursor, data):
        self._keys = tuple(col[0] for col in cursor.description)
        self._data = tuple(data)
    def __getitem__(self, key):
        if isinstance(key, int):
            return self._data[key]
        return self._data[self._keys.index(key)]
    def keys(self):
        return list(self._keys)
    def __iter__(self):
        return iter(zip(self._keys, self._data))
    def __len__(self):
        return len(self._data)

class _RowCursor:
    """Cursor wrapper — aplikuje _Row factory na výsledky libsql_experimental."""
    def __init__(self, cur):
        self._cur = cur
    @property
    def description(self):
        return self._cur.description
    def fetchone(self):
        row = self._cur.fetchone()
        return _Row(self._cur, row) if row is not None else None
    def fetchall(self):
        rows = self._cur.fetchall()
        return [_Row(self._cur, r) for r in rows]
    def __iter__(self):
        for row in self._cur.fetchall():
            yield _Row(self._cur, row)

class _LibsqlConnectionWrapper:
    """Connection wrapper pre libsql_experimental (nepodporuje row_factory)."""
    def __init__(self, con):
        self._con = con
    def execute(self, sql, params=()):
        return _RowCursor(self._con.execute(sql, params))
    def executemany(self, sql, seq):
        self._con.executemany(sql, seq)
    def commit(self):
        self._con.commit()
    def close(self):
        self._con.close()
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._con.commit()
        return False

def get_db():
    if TURSO_URL and TURSO_TOKEN:
        import libsql_experimental as libsql
        con = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
        return _LibsqlConnectionWrapper(con)
    else:
        con = sqlite3.connect(str(DB_PATH))
        con.execute("PRAGMA journal_mode=WAL")
        con.row_factory = _Row
        return con

def init_db():
    with get_db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            value TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS parameters (
            key TEXT PRIMARY KEY,
            value REAL NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS trades (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            submitted_by TEXT NOT NULL,
            instrument TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            trend_context TEXT NOT NULL,
            htf_confirmed TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            idea_description TEXT NOT NULL,
            condition1 TEXT,
            condition2 TEXT,
            condition3 TEXT,
            condition4 TEXT,
            entry_price REAL,
            stop_loss REAL,
            take_profit REAL,
            image_data TEXT,
            trade_status TEXT NOT NULL DEFAULT 'Otvorený',
            close_date TEXT,
            result_pips REAL,
            result_rr REAL,
            result_note TEXT,
            status TEXT NOT NULL DEFAULT 'Aktívny'
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS reviews (
            id TEXT PRIMARY KEY,
            trade_id TEXT NOT NULL REFERENCES trades(id),
            reviewed_at TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            proposed_entry REAL,
            proposed_sl REAL,
            proposed_tp REAL,
            rrr REAL,
            fixed_plan TEXT,
            proposed_risk_pct REAL,
            custom_condition1 TEXT,
            custom_condition1_met TEXT,
            custom_condition2 TEXT,
            custom_condition2_met TEXT,
            custom_condition3 TEXT,
            custom_condition3_met TEXT,
            comment TEXT,
            verdict TEXT NOT NULL,
            UNIQUE(trade_id, reviewer)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'trader',
            trader_name TEXT,
            created_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS conditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL DEFAULT 'team',
            trader TEXT,
            text TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            type TEXT NOT NULL,
            message TEXT NOT NULL,
            trade_id TEXT,
            read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS trade_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT NOT NULL REFERENCES trades(id),
            image_data TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""")

        # Migrate existing trades table (add new columns if missing)
        migrations = [
            "ALTER TABLE trades ADD COLUMN entry_price REAL",
            "ALTER TABLE trades ADD COLUMN stop_loss REAL",
            "ALTER TABLE trades ADD COLUMN take_profit REAL",
            "ALTER TABLE trades ADD COLUMN image_data TEXT",
            "ALTER TABLE trades ADD COLUMN trade_status TEXT DEFAULT 'Otvorený'",
            "ALTER TABLE trades ADD COLUMN close_date TEXT",
            "ALTER TABLE trades ADD COLUMN result_pips REAL",
            "ALTER TABLE trades ADD COLUMN result_rr REAL",
            "ALTER TABLE trades ADD COLUMN result_note TEXT",
        ]
        for sql in migrations:
            try:
                con.execute(sql)
            except Exception:
                pass

        # Default config
        existing = con.execute("SELECT COUNT(*) FROM config").fetchone()[0]
        if existing == 0:
            con.executemany("INSERT INTO config (category, value) VALUES (?,?)", [
                ('Instrument','EUR/USD'), ('Instrument','GBP/USD'), ('Instrument','USD/JPY'),
                ('Instrument','Gold'), ('Instrument','Silver'), ('Instrument','BTC/USD'),
                ('Instrument','US30'), ('Instrument','NAS100'),
                ('Trader','Tomino'), ('Trader','Maroš'), ('Trader','Miloš'), ('Trader','Topier'),
            ])
        existing_params = con.execute("SELECT COUNT(*) FROM parameters").fetchone()[0]
        if existing_params == 0:
            con.executemany("INSERT INTO parameters (key, value) VALUES (?,?)", [
                ('min_rrr', 1.5), ('max_risk_trend', 1.0), ('max_risk_counter', 0.5),
                ('min_approvals', 2.0),
            ])
        # Add min_approvals if missing from existing DBs
        try:
            con.execute("INSERT OR IGNORE INTO parameters (key, value) VALUES ('min_approvals', 2.0)")
        except Exception:
            pass

        # Default users (create if none exist)
        existing_users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if existing_users == 0:
            default_password = 'tmtm2024'
            traders = [('tomino', 'Tomino'), ('maros', 'Maroš'), ('milos', 'Miloš'), ('topier', 'Topier')]
            for username, trader_name in traders:
                salt = secrets.token_hex(16)
                pwd_hash = hashlib.sha256((salt + default_password).encode()).hexdigest()
                con.execute(
                    "INSERT INTO users (username, password_hash, salt, role, trader_name, created_at) VALUES (?,?,?,'trader',?,?)",
                    (username, pwd_hash, salt, trader_name, str(date.today())))
            # Admin user
            salt = secrets.token_hex(16)
            pwd_hash = hashlib.sha256((salt + 'admin123').encode()).hexdigest()
            con.execute(
                "INSERT INTO users (username, password_hash, salt, role, trader_name, created_at) VALUES ('admin',?,?,'admin',NULL,?)",
                (pwd_hash, salt, str(date.today())))

def rows_to_list(rows):
    return [dict(r) for r in rows]

# ─── AUTH ──────────────────────────────────────────────────────────────────────

def hash_password(salt, password):
    return hashlib.sha256((salt + password).encode()).hexdigest()

def login(username, password):
    with get_db() as con:
        user = con.execute("SELECT * FROM users WHERE username=?", (username.lower().strip(),)).fetchone()
        if not user:
            return None, "Používateľ nenájdený"
        user = dict(user)
        if user['password_hash'] != hash_password(user['salt'], password):
            return None, "Nesprávne heslo"
        token = secrets.token_hex(32)
        con.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
                    (token, user['id'], str(datetime.now())))
        del user['password_hash']
        del user['salt']
        return token, user

def logout(token):
    with get_db() as con:
        con.execute("DELETE FROM sessions WHERE token=?", (token,))

def get_session_user(token):
    if not token:
        return None
    with get_db() as con:
        row = con.execute(
            "SELECT u.id, u.username, u.role, u.trader_name FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token=?",
            (token,)).fetchone()
        return dict(row) if row else None

def get_all_users():
    with get_db() as con:
        rows = con.execute("SELECT id, username, role, trader_name, created_at FROM users ORDER BY id").fetchall()
        return rows_to_list(rows)

def create_user(data):
    salt = secrets.token_hex(16)
    pwd_hash = hash_password(salt, data['password'])
    with get_db() as con:
        con.execute(
            "INSERT INTO users (username, password_hash, salt, role, trader_name, created_at) VALUES (?,?,?,?,?,?)",
            (data['username'].lower().strip(), pwd_hash, salt,
             data.get('role', 'trader'), data.get('trader_name') or None, str(date.today())))

def reset_password(user_id, new_password):
    salt = secrets.token_hex(16)
    pwd_hash = hash_password(salt, new_password)
    with get_db() as con:
        con.execute("UPDATE users SET password_hash=?, salt=? WHERE id=?", (pwd_hash, salt, user_id))

def delete_user(user_id):
    with get_db() as con:
        con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        con.execute("DELETE FROM users WHERE id=?", (user_id,))

# ─── CONFIG ────────────────────────────────────────────────────────────────────

def get_traders():
    with get_db() as con:
        return [r[0] for r in con.execute("SELECT value FROM config WHERE category='Trader' ORDER BY value")]

def get_instruments():
    with get_db() as con:
        return [r[0] for r in con.execute("SELECT value FROM config WHERE category='Instrument' ORDER BY value")]

def get_parameters():
    with get_db() as con:
        rows = con.execute("SELECT key, value FROM parameters").fetchall()
        return {r[0]: r[1] for r in rows}

# ─── TRADES ────────────────────────────────────────────────────────────────────

def next_trade_id():
    with get_db() as con:
        last = con.execute("SELECT id FROM trades ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        if not last: return "TR-001"
        num = int(last[0].split('-')[1]) + 1
        return f"TR-{num:03d}"

def next_review_id():
    with get_db() as con:
        last = con.execute("SELECT id FROM reviews ORDER BY reviewed_at DESC, id DESC LIMIT 1").fetchone()
        if not last: return "HV-001"
        num = int(last[0].split('-')[1]) + 1
        return f"HV-{num:03d}"

def calc_team_verdict(approved, revision, rejected, min_approvals=2):
    total = approved + revision + rejected
    if total == 0: return "Bez hodnotenia"
    if approved >= int(min_approvals): return "Obchodovať"
    if approved >= 1 and revision >= 1 and rejected == 0: return "Na potvrdenie"
    return "Neobchodovať"

def enrich_trade(trade_dict, reviews, min_approvals=2):
    approved = sum(1 for r in reviews if r['verdict'] == 'Schválené')
    revision  = sum(1 for r in reviews if r['verdict'] == 'Na revíziu')
    rejected  = sum(1 for r in reviews if r['verdict'] == 'Zamietnuté')
    entries = [r['proposed_entry'] for r in reviews if r['proposed_entry']]
    sls     = [r['proposed_sl']    for r in reviews if r['proposed_sl']]
    tps     = [r['proposed_tp']    for r in reviews if r['proposed_tp']]
    rrrs    = [r['rrr']            for r in reviews if r['rrr']]
    total   = len(reviews)
    return {
        **trade_dict,
        'reviews': reviews,
        'approved': approved, 'revision': revision, 'rejected': rejected,
        'review_count': total,
        'approved_pct': round(approved/total*100) if total else 0,
        'revision_pct': round(revision/total*100) if total else 0,
        'rejected_pct': round(rejected/total*100) if total else 0,
        'team_verdict': calc_team_verdict(approved, revision, rejected, min_approvals),
        'avg_entry': round(sum(entries)/len(entries), 5) if entries else None,
        'avg_sl':    round(sum(sls)/len(sls), 5)         if sls    else None,
        'avg_tp':    round(sum(tps)/len(tps), 5)         if tps    else None,
        'avg_rrr':   round(sum(rrrs)/len(rrrs), 2)       if rrrs   else None,
    }

def get_all_trades():
    params = get_parameters()
    min_approvals = int(params.get('min_approvals', 2))
    with get_db() as con:
        trades = rows_to_list(con.execute("SELECT * FROM trades ORDER BY created_at DESC, id DESC").fetchall())
        for t in trades:
            t.pop('image_data', None)  # exclude heavy image data from list
            reviews = rows_to_list(con.execute("SELECT * FROM reviews WHERE trade_id=? ORDER BY reviewed_at", (t['id'],)).fetchall())
            t.update(enrich_trade(t, reviews, min_approvals))
        return trades

def get_trade(trade_id):
    params = get_parameters()
    min_approvals = int(params.get('min_approvals', 2))
    with get_db() as con:
        t = con.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if not t: return None
        trade = dict(t)
        reviews = rows_to_list(con.execute("SELECT * FROM reviews WHERE trade_id=? ORDER BY reviewed_at", (trade_id,)).fetchall())
        # Collect all images: legacy image_data + trade_images table
        images = []
        if trade.get('image_data'):
            images.append({'id': 0, 'image_data': trade.pop('image_data'), 'position': -1})
        else:
            trade.pop('image_data', None)
        extra = rows_to_list(con.execute(
            "SELECT id, image_data, position FROM trade_images WHERE trade_id=? ORDER BY position, id",
            (trade_id,)).fetchall())
        images.extend(extra)
        result = enrich_trade(trade, reviews, min_approvals)
        result['images'] = images
        return result

def create_trade(data):
    tid = next_trade_id()
    today = str(date.today())
    entry_price = float(data['entry_price']) if data.get('entry_price') else None
    stop_loss   = float(data['stop_loss'])   if data.get('stop_loss')   else None
    take_profit = float(data['take_profit']) if data.get('take_profit') else None
    with get_db() as con:
        con.execute("""INSERT INTO trades
            (id, created_at, submitted_by, instrument, timeframe, direction,
             trend_context, htf_confirmed, entry_type, idea_description,
             condition1, condition2, condition3, condition4,
             entry_price, stop_loss, take_profit,
             trade_status, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'Otvorený','Aktívny')""",
            (tid, today, data['submitted_by'], data['instrument'], data['timeframe'],
             data['direction'], data['trend_context'], data['htf_confirmed'],
             data['entry_type'], data['idea_description'],
             data.get('condition1') or None, data.get('condition2') or None,
             data.get('condition3') or None, data.get('condition4') or None,
             entry_price, stop_loss, take_profit))
    # Notify all users about the new trade
    submitter_id = get_user_id_by_trader_name(data['submitted_by'])
    notify_all_users(
        'new_trade',
        f"Nový obchod {tid} od {data['submitted_by']}: {data['instrument']} {data['direction']}",
        trade_id=tid,
        exclude_user_id=submitter_id)
    return tid

def upload_trade_image(trade_id, image_data):
    with get_db() as con:
        con.execute("UPDATE trades SET image_data=? WHERE id=?", (image_data, trade_id))

def add_trade_image(trade_id, image_data):
    with get_db() as con:
        max_pos = con.execute(
            "SELECT COALESCE(MAX(position),0) FROM trade_images WHERE trade_id=?", (trade_id,)).fetchone()[0]
        con.execute(
            "INSERT INTO trade_images (trade_id, image_data, position, created_at) VALUES (?,?,?,?)",
            (trade_id, image_data, int(max_pos)+1, str(datetime.now())))

def delete_trade_image(trade_id, img_id):
    with get_db() as con:
        con.execute("DELETE FROM trade_images WHERE id=? AND trade_id=?", (img_id, trade_id))

def close_trade(trade_id, data):
    result_pips = float(data['result_pips']) if data.get('result_pips') else None
    result_rr   = float(data['result_rr'])   if data.get('result_rr')   else None
    result_note = data.get('result_note') or None
    trade_status = data.get('trade_status', 'Uzatvorený')
    with get_db() as con:
        con.execute(
            "UPDATE trades SET trade_status=?, close_date=?, result_pips=?, result_rr=?, result_note=? WHERE id=?",
            (trade_status, str(date.today()), result_pips, result_rr, result_note, trade_id))

def submit_review(trade_id, data):
    with get_db() as con:
        trade = con.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        if not trade: raise ValueError("Trade not found")

    rid = next_review_id()
    today = str(date.today())
    entry = float(data['proposed_entry']) if data.get('proposed_entry') else None
    sl    = float(data['proposed_sl'])    if data.get('proposed_sl')    else None
    tp    = float(data['proposed_tp'])    if data.get('proposed_tp')    else None

    rrr = None
    if entry is not None and sl is not None and tp is not None:
        risk = abs(entry - sl)
        if risk > 0:
            rrr = round(abs(tp - entry) / risk, 4)

    risk_pct = float(data['proposed_risk_pct']) if data.get('proposed_risk_pct') else None

    with get_db() as con:
        con.execute("""INSERT OR REPLACE INTO reviews
            (id, trade_id, reviewed_at, reviewer, proposed_entry, proposed_sl, proposed_tp,
             rrr, fixed_plan, proposed_risk_pct,
             custom_condition1, custom_condition1_met,
             custom_condition2, custom_condition2_met,
             custom_condition3, custom_condition3_met,
             comment, verdict)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, trade_id, today, data['reviewer'], entry, sl, tp, rrr,
             data.get('fixed_plan') or None, risk_pct,
             data.get('custom_condition1') or None, data.get('custom_condition1_met') or None,
             data.get('custom_condition2') or None, data.get('custom_condition2_met') or None,
             data.get('custom_condition3') or None, data.get('custom_condition3_met') or None,
             data.get('comment') or None, data['verdict']))
        # Fetch trade submitter to notify them
        trade_row = con.execute("SELECT submitted_by FROM trades WHERE id=?", (trade_id,)).fetchone()
        submitter_name = trade_row[0] if trade_row else None

    # Notify trade submitter about the new review
    if submitter_name:
        submitter_id = get_user_id_by_trader_name(submitter_name)
        if submitter_id:
            verdict_label = data['verdict']
            create_notification(
                submitter_id, 'new_review',
                f"Obchod {trade_id} dostal hodnotenie od {data['reviewer']}: {verdict_label}",
                trade_id=trade_id)

    # Check if team verdict changed to "Obchodovať" and notify submitter
    enriched = get_trade(trade_id)  # already uses min_approvals from parameters
    if enriched and enriched.get('team_verdict') == 'Obchodovať' and submitter_name:
        submitter_id = get_user_id_by_trader_name(submitter_name)
        if submitter_id:
            create_notification(
                submitter_id, 'trade_approved',
                f"Obchod {trade_id} ({enriched.get('instrument','')}) bol schválený tímom! Môžeš obchodovať.",
                trade_id=trade_id)

    return rid

def get_dashboard():
    with get_db() as con:
        total_trades  = con.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        total_reviews = con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        approved_rev  = con.execute("SELECT COUNT(*) FROM reviews WHERE verdict='Schválené'").fetchone()[0]
        revision_rev  = con.execute("SELECT COUNT(*) FROM reviews WHERE verdict='Na revíziu'").fetchone()[0]
        rejected_rev  = con.execute("SELECT COUNT(*) FROM reviews WHERE verdict='Zamietnuté'").fetchone()[0]
        avg_rrr_row   = con.execute("SELECT AVG(rrr) FROM reviews WHERE rrr IS NOT NULL").fetchone()[0]
        avg_risk_row  = con.execute("SELECT AVG(proposed_risk_pct) FROM reviews WHERE proposed_risk_pct IS NOT NULL").fetchone()[0]
    trades = get_all_trades()
    return {
        'total_trades': total_trades, 'total_reviews': total_reviews,
        'approved_reviews': approved_rev, 'revision_reviews': revision_rev, 'rejected_reviews': rejected_rev,
        'avg_rrr':  round(avg_rrr_row, 2) if avg_rrr_row else 0,
        'avg_risk': round(avg_risk_row, 2) if avg_risk_row else 0,
        'trade_to_go':   sum(1 for t in trades if t['team_verdict'] == 'Obchodovať'),
        'trade_confirm': sum(1 for t in trades if t['team_verdict'] == 'Na potvrdenie'),
        'trade_no':      sum(1 for t in trades if t['team_verdict'] == 'Neobchodovať'),
        'trade_none':    sum(1 for t in trades if t['team_verdict'] == 'Bez hodnotenia'),
        'open_trades':   sum(1 for t in trades if t.get('trade_status') == 'Otvorený'),
        'closed_trades': sum(1 for t in trades if t.get('trade_status') == 'Uzatvorený'),
    }

def get_trader_stats():
    traders = get_traders()
    stats = []
    with get_db() as con:
        for trader in traders:
            submitted = con.execute("SELECT COUNT(*) FROM trades WHERE submitted_by=?", (trader,)).fetchone()[0]
            closed_trades = rows_to_list(con.execute(
                "SELECT * FROM trades WHERE submitted_by=? AND trade_status='Uzatvorený'", (trader,)).fetchall())
            total_closed = len(closed_trades)
            profitable = sum(1 for t in closed_trades if t.get('result_pips') and t['result_pips'] > 0)
            win_rate = round(profitable / total_closed * 100, 1) if total_closed > 0 else None
            total_pips = sum(t['result_pips'] for t in closed_trades if t.get('result_pips')) if closed_trades else 0
            rrs = [t['result_rr'] for t in closed_trades if t.get('result_rr')]
            avg_rr_closed = round(sum(rrs) / len(rrs), 2) if rrs else None

            reviews_given = con.execute("SELECT COUNT(*) FROM reviews WHERE reviewer=?", (trader,)).fetchone()[0]
            avg_rrr_row = con.execute("SELECT AVG(rrr) FROM reviews WHERE reviewer=? AND rrr IS NOT NULL", (trader,)).fetchone()[0]
            approved_given = con.execute("SELECT COUNT(*) FROM reviews WHERE reviewer=? AND verdict='Schválené'", (trader,)).fetchone()[0]

            stats.append({
                'trader': trader,
                'trades_submitted': submitted,
                'trades_closed': total_closed,
                'trades_profitable': profitable,
                'win_rate': win_rate,
                'total_pips': round(total_pips, 1) if total_pips else 0,
                'avg_rr_closed': avg_rr_closed,
                'reviews_given': reviews_given,
                'approved_given': approved_given,
                'avg_rrr_proposed': round(avg_rrr_row, 2) if avg_rrr_row else None,
            })
    return stats

# ─── CONDITIONS ────────────────────────────────────────────────────────────────

def get_conditions(ctype=None, trader=None):
    with get_db() as con:
        if ctype == 'team':
            rows = con.execute(
                "SELECT * FROM conditions WHERE type='team' ORDER BY position, id").fetchall()
        elif ctype == 'personal' and trader:
            rows = con.execute(
                "SELECT * FROM conditions WHERE type='personal' AND trader=? ORDER BY position, id",
                (trader,)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM conditions ORDER BY type, position, id").fetchall()
        return rows_to_list(rows)

def create_condition(data):
    with get_db() as con:
        # auto-position: place at end
        max_pos = con.execute(
            "SELECT COALESCE(MAX(position),0) FROM conditions WHERE type=? AND (trader=? OR trader IS NULL)",
            (data.get('type','team'), data.get('trader'))).fetchone()[0]
        con.execute(
            "INSERT INTO conditions (type, trader, text, position, created_at) VALUES (?,?,?,?,?)",
            (data.get('type','team'), data.get('trader') or None,
             data['text'].strip(), int(max_pos) + 1, str(datetime.now())))
        return con.execute("SELECT last_insert_rowid()").fetchone()[0]

def delete_condition(cid):
    with get_db() as con:
        con.execute("DELETE FROM conditions WHERE id=?", (cid,))

# ─── NOTIFICATIONS ─────────────────────────────────────────────────────────────

def get_notifications(user_id):
    with get_db() as con:
        rows = con.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
            (user_id,)).fetchall()
        return rows_to_list(rows)

def get_unread_count(user_id):
    with get_db() as con:
        return con.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0",
            (user_id,)).fetchone()[0]

def mark_all_read(user_id):
    with get_db() as con:
        con.execute("UPDATE notifications SET read=1 WHERE user_id=?", (user_id,))

def create_notification(user_id, ntype, message, trade_id=None):
    with get_db() as con:
        con.execute(
            "INSERT INTO notifications (user_id, type, message, trade_id, read, created_at) VALUES (?,?,?,?,0,?)",
            (user_id, ntype, message, trade_id, str(datetime.now())))

def notify_all_users(ntype, message, trade_id=None, exclude_user_id=None):
    """Send a notification to all users (optionally excluding one)."""
    with get_db() as con:
        users = con.execute("SELECT id FROM users").fetchall()
        for u in users:
            uid = u[0]
            if exclude_user_id and uid == exclude_user_id:
                continue
            con.execute(
                "INSERT INTO notifications (user_id, type, message, trade_id, read, created_at) VALUES (?,?,?,?,0,?)",
                (uid, ntype, message, trade_id, str(datetime.now())))

def get_user_id_by_trader_name(trader_name):
    with get_db() as con:
        row = con.execute("SELECT id FROM users WHERE trader_name=?", (trader_name,)).fetchone()
        return row[0] if row else None

# ─── HTTP HANDLER ──────────────────────────────────────────────────────────────

CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.json': 'application/json',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.ico':  'image/x-icon',
}

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def get_token(self):
        return self.headers.get('X-Auth-Token') or self.headers.get('x-auth-token')

    def get_current_user(self):
        return get_session_user(self.get_token())

    def require_auth(self):
        user = self.get_current_user()
        if not user:
            self.send_json({'error': 'Nie si prihlásený'}, 401)
        return user

    def require_admin(self):
        user = self.get_current_user()
        if not user:
            self.send_json({'error': 'Nie si prihlásený'}, 401)
            return None
        if user['role'] != 'admin':
            self.send_json({'error': 'Vyžaduje sa admin oprávnenie'}, 403)
            return None
        return user

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Auth-Token')
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, msg, status=400):
        self.send_json({'error': msg}, status)

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length == 0: return {}
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, PATCH, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Auth-Token')
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.rstrip('/')
        if not path: path = '/'

        if path == '/api/config':
            return self.send_json({'traders': get_traders(), 'instruments': get_instruments(), 'parameters': get_parameters()})

        if path == '/api/auth/me':
            user = self.get_current_user()
            return self.send_json({'user': user})

        if path == '/api/dashboard':
            return self.send_json(get_dashboard())

        if path == '/api/trades':
            return self.send_json(get_all_trades())

        if path == '/api/stats/traders':
            return self.send_json(get_trader_stats())

        if path == '/api/conditions':
            user = self.require_auth()
            if not user: return
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ctype = qs.get('type', [None])[0]
            trader = qs.get('trader', [None])[0]
            return self.send_json(get_conditions(ctype, trader))

        if path == '/api/notifications':
            user = self.require_auth()
            if not user: return
            notifs = get_notifications(user['id'])
            unread = get_unread_count(user['id'])
            return self.send_json({'notifications': notifs, 'unread': unread})

        if path == '/api/users':
            user = self.require_admin()
            if not user: return
            return self.send_json(get_all_users())

        m = re.match(r'^/api/trades/([^/]+)$', path)
        if m:
            t = get_trade(m.group(1))
            return self.send_json(t) if t else self.send_error_json('Not found', 404)

        m = re.match(r'^/api/trades/([^/]+)/images$', path)
        if m:
            user = self.require_auth()
            if not user: return
            trade_id = m.group(1)
            with get_db() as con:
                imgs = rows_to_list(con.execute(
                    "SELECT id, position FROM trade_images WHERE trade_id=? ORDER BY position, id",
                    (trade_id,)).fetchall())
            return self.send_json(imgs)

        # Static files
        if path == '/':
            file_path = PUBLIC_DIR / 'index.html'
        else:
            file_path = PUBLIC_DIR / path.lstrip('/')

        if file_path.exists() and file_path.is_file():
            ext = file_path.suffix.lower()
            ct = CONTENT_TYPES.get(ext, 'application/octet-stream')
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', len(data))
            self.end_headers()
            self.wfile.write(data)
        else:
            index = PUBLIC_DIR / 'index.html'
            data = index.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(data))
            self.end_headers()
            self.wfile.write(data)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path.rstrip('/')
        try:
            body = self.read_body()

            if path == '/api/auth/login':
                username = body.get('username', '').strip()
                password = body.get('password', '')
                if not username or not password:
                    return self.send_error_json('Vyplň meno a heslo')
                token, result = login(username, password)
                if not token:
                    return self.send_error_json(result, 401)
                return self.send_json({'token': token, 'user': result})

            if path == '/api/auth/logout':
                token = self.get_token()
                if token: logout(token)
                return self.send_json({'ok': True})

            if path == '/api/users':
                user = self.require_admin()
                if not user: return
                for f in ['username', 'password']:
                    if not body.get(f): return self.send_error_json(f'Pole {f} je povinné')
                try:
                    create_user(body)
                    return self.send_json({'ok': True}, 201)
                except Exception as e:
                    return self.send_error_json(f'Chyba: {e}')

            if path == '/api/conditions':
                user = self.require_auth()
                if not user: return
                ctype = body.get('type', 'team')
                # Only admins can create team conditions
                if ctype == 'team' and user['role'] != 'admin':
                    return self.send_error_json('Iba admin môže pridávať tímové podmienky', 403)
                if not body.get('text', '').strip():
                    return self.send_error_json('Text podmienky je povinný')
                # For personal conditions, associate with current trader
                if ctype == 'personal':
                    body['trader'] = user.get('trader_name') or user['username']
                cid = create_condition(body)
                return self.send_json({'id': cid}, 201)

            if path == '/api/notifications/read':
                user = self.require_auth()
                if not user: return
                mark_all_read(user['id'])
                return self.send_json({'ok': True})

            if path == '/api/config/traders':
                user = self.require_admin()
                if not user: return
                name = body.get('name', '').strip()
                if not name: return self.send_error_json('Meno je povinné')
                with get_db() as con:
                    exists = con.execute("SELECT 1 FROM config WHERE category='Trader' AND value=?", (name,)).fetchone()
                    if not exists: con.execute("INSERT INTO config (category, value) VALUES ('Trader',?)", (name,))
                return self.send_json({'ok': True})

            if path == '/api/config/instruments':
                user = self.require_admin()
                if not user: return
                name = body.get('name', '').strip()
                if not name: return self.send_error_json('Názov je povinný')
                with get_db() as con:
                    exists = con.execute("SELECT 1 FROM config WHERE category='Instrument' AND value=?", (name,)).fetchone()
                    if not exists: con.execute("INSERT INTO config (category, value) VALUES ('Instrument',?)", (name,))
                return self.send_json({'ok': True})

            if path == '/api/trades':
                user = self.require_auth()
                if not user: return
                if user.get('trader_name'):
                    body['submitted_by'] = user['trader_name']
                for f in ['submitted_by', 'instrument', 'timeframe', 'direction',
                          'trend_context', 'htf_confirmed', 'entry_type', 'idea_description']:
                    if not body.get(f): return self.send_error_json(f'Pole {f} je povinné')
                tid = create_trade(body)
                return self.send_json({'id': tid}, 201)

            m = re.match(r'^/api/trades/([^/]+)/reviews$', path)
            if m:
                user = self.require_auth()
                if not user: return
                trade_id = m.group(1)
                if user.get('trader_name'):
                    body['reviewer'] = user['trader_name']
                if not body.get('reviewer'): return self.send_error_json('Hodnotiteľ je povinný')
                if not body.get('verdict'):  return self.send_error_json('Verdikt je povinný')
                rid = submit_review(trade_id, body)
                return self.send_json({'id': rid}, 201)

            m = re.match(r'^/api/trades/([^/]+)/image$', path)
            if m:
                user = self.require_auth()
                if not user: return
                trade_id = m.group(1)
                image_data = body.get('image_data')
                if not image_data: return self.send_error_json('Chýba image_data')
                upload_trade_image(trade_id, image_data)
                return self.send_json({'ok': True})

            m = re.match(r'^/api/trades/([^/]+)/images$', path)
            if m:
                user = self.require_auth()
                if not user: return
                trade_id = m.group(1)
                image_data = body.get('image_data')
                if not image_data: return self.send_error_json('Chýba image_data')
                add_trade_image(trade_id, image_data)
                return self.send_json({'ok': True}, 201)

            self.send_error_json('Not found', 404)
        except Exception as e:
            self.send_error_json(str(e), 500)

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path.rstrip('/')
        try:
            body = self.read_body()

            if path == '/api/config/parameters':
                user = self.require_admin()
                if not user: return
                with get_db() as con:
                    for k, v in body.items():
                        con.execute("INSERT OR REPLACE INTO parameters (key, value) VALUES (?,?)", (k, float(v)))
                return self.send_json({'ok': True})

            m = re.match(r'^/api/users/(\d+)/password$', path)
            if m:
                user = self.require_auth()
                if not user: return
                target_id = int(m.group(1))
                if user['role'] != 'admin' and user['id'] != target_id:
                    return self.send_error_json('Nedostatočné oprávnenia', 403)
                new_pwd = body.get('password', '')
                if len(new_pwd) < 6: return self.send_error_json('Heslo musí mať aspoň 6 znakov')
                reset_password(target_id, new_pwd)
                return self.send_json({'ok': True})

            self.send_error_json('Not found', 404)
        except Exception as e:
            self.send_error_json(str(e), 500)

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path.rstrip('/')
        try:
            m = re.match(r'^/api/config/traders/(.+)$', path)
            if m:
                user = self.require_admin()
                if not user: return
                name = urllib.parse.unquote(m.group(1))
                with get_db() as con:
                    con.execute("DELETE FROM config WHERE category='Trader' AND value=?", (name,))
                return self.send_json({'ok': True})

            m = re.match(r'^/api/config/instruments/(.+)$', path)
            if m:
                user = self.require_admin()
                if not user: return
                name = urllib.parse.unquote(m.group(1))
                with get_db() as con:
                    con.execute("DELETE FROM config WHERE category='Instrument' AND value=?", (name,))
                return self.send_json({'ok': True})

            m = re.match(r'^/api/users/(\d+)$', path)
            if m:
                user = self.require_admin()
                if not user: return
                delete_user(int(m.group(1)))
                return self.send_json({'ok': True})

            m = re.match(r'^/api/trades/([^/]+)/images/(\d+)$', path)
            if m:
                user = self.require_auth()
                if not user: return
                trade_id, img_id = m.group(1), int(m.group(2))
                delete_trade_image(trade_id, img_id)
                return self.send_json({'ok': True})

            m = re.match(r'^/api/conditions/(\d+)$', path)
            if m:
                user = self.require_auth()
                if not user: return
                cid = int(m.group(1))
                # Check ownership
                with get_db() as con:
                    cond = con.execute("SELECT * FROM conditions WHERE id=?", (cid,)).fetchone()
                if not cond:
                    return self.send_error_json('Podmienka nenájdená', 404)
                cond = dict(cond)
                if cond['type'] == 'team' and user['role'] != 'admin':
                    return self.send_error_json('Iba admin môže mazať tímové podmienky', 403)
                if cond['type'] == 'personal':
                    my_name = user.get('trader_name') or user['username']
                    if cond['trader'] != my_name and user['role'] != 'admin':
                        return self.send_error_json('Nemáš oprávnenie', 403)
                delete_condition(cid)
                return self.send_json({'ok': True})

            self.send_error_json('Not found', 404)
        except Exception as e:
            self.send_error_json(str(e), 500)

    def do_PATCH(self):
        path = urllib.parse.urlparse(self.path).path.rstrip('/')
        try:
            body = self.read_body()

            m = re.match(r'^/api/trades/([^/]+)/status$', path)
            if m:
                user = self.require_auth()
                if not user: return
                trade_id = m.group(1)
                with get_db() as con:
                    con.execute("UPDATE trades SET status=? WHERE id=?", (body['status'], trade_id))
                return self.send_json({'ok': True})

            m = re.match(r'^/api/trades/([^/]+)/close$', path)
            if m:
                user = self.require_auth()
                if not user: return
                trade_id = m.group(1)
                close_trade(trade_id, body)
                return self.send_json({'ok': True})

            self.send_error_json('Not found', 404)
        except Exception as e:
            self.send_error_json(str(e), 500)

# ─── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    server = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f"\n🚀 TMTM Trading Platform v2 beží na http://localhost:{PORT}")
    print(f"   Databáza: {DB_PATH}")
    print(f"   Predvolené účty: tomino/maros/milos/topier (heslo: tmtm2024), admin (heslo: admin123)")
    print(f"   Ukončenie: Ctrl+C\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer zastavený.")
        server.shutdown()
