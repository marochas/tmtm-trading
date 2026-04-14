const Database = require('better-sqlite3');
const path = require('path');

const DB_PATH = path.join(__dirname, 'trading.db');
const db = new Database(DB_PATH);

// Enable WAL mode for better concurrent access
db.pragma('journal_mode = WAL');

function initDB() {
  db.exec(`
    -- Config table for traders, instruments, parameters
    CREATE TABLE IF NOT EXISTS config (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      category TEXT NOT NULL,
      value TEXT NOT NULL
    );

    -- Parameters table
    CREATE TABLE IF NOT EXISTS parameters (
      key TEXT PRIMARY KEY,
      value REAL NOT NULL
    );

    -- Trade ideas
    CREATE TABLE IF NOT EXISTS trades (
      id TEXT PRIMARY KEY,
      created_at TEXT NOT NULL,
      submitted_by TEXT NOT NULL,
      instrument TEXT NOT NULL,
      timeframe TEXT NOT NULL,
      direction TEXT NOT NULL CHECK(direction IN ('Long', 'Short')),
      trend_context TEXT NOT NULL CHECK(trend_context IN ('Do trendu', 'Proti trendu')),
      htf_confirmed TEXT NOT NULL CHECK(htf_confirmed IN ('Áno', 'Nie')),
      entry_type TEXT NOT NULL CHECK(entry_type IN ('Limit', 'Market')),
      idea_description TEXT NOT NULL,
      condition1 TEXT,
      condition2 TEXT,
      condition3 TEXT,
      condition4 TEXT,
      status TEXT NOT NULL DEFAULT 'Aktívny' CHECK(status IN ('Aktívny', 'Uzavretý', 'Archivovaný'))
    );

    -- Team reviews
    CREATE TABLE IF NOT EXISTS reviews (
      id TEXT PRIMARY KEY,
      trade_id TEXT NOT NULL REFERENCES trades(id),
      reviewed_at TEXT NOT NULL,
      reviewer TEXT NOT NULL,
      proposed_entry REAL,
      proposed_sl REAL,
      proposed_tp REAL,
      rrr REAL,
      fixed_plan TEXT CHECK(fixed_plan IN ('Áno', 'Nie')),
      proposed_risk_pct REAL,
      custom_condition1 TEXT,
      custom_condition1_met TEXT CHECK(custom_condition1_met IN ('Áno', 'Nie')),
      custom_condition2 TEXT,
      custom_condition2_met TEXT CHECK(custom_condition2_met IN ('Áno', 'Nie')),
      custom_condition3 TEXT,
      custom_condition3_met TEXT CHECK(custom_condition3_met IN ('Áno', 'Nie')),
      comment TEXT,
      verdict TEXT NOT NULL CHECK(verdict IN ('Schválené', 'Na revíziu', 'Zamietnuté')),
      UNIQUE(trade_id, reviewer)
    );

    -- Insert default config if not exists
    INSERT OR IGNORE INTO config (category, value) VALUES
      ('Instrument', 'EUR/USD'),
      ('Instrument', 'GBP/USD'),
      ('Instrument', 'USD/JPY'),
      ('Instrument', 'Gold'),
      ('Instrument', 'Silver'),
      ('Instrument', 'BTC/USD'),
      ('Instrument', 'US30'),
      ('Instrument', 'NAS100'),
      ('Trader', 'Tomino'),
      ('Trader', 'Maroš'),
      ('Trader', 'Miloš'),
      ('Trader', 'Topier');

    -- Insert default parameters
    INSERT OR IGNORE INTO parameters (key, value) VALUES
      ('min_rrr', 1.5),
      ('max_risk_trend', 1.0),
      ('max_risk_counter', 0.5);
  `);
}

function getTraders() {
  return db.prepare("SELECT value FROM config WHERE category = 'Trader' ORDER BY value").all().map(r => r.value);
}

function getInstruments() {
  return db.prepare("SELECT value FROM config WHERE category = 'Instrument' ORDER BY value").all().map(r => r.value);
}

function getParameters() {
  const rows = db.prepare("SELECT key, value FROM parameters").all();
  const params = {};
  rows.forEach(r => { params[r.key] = r.value; });
  return params;
}

function updateParameters(params) {
  const stmt = db.prepare("INSERT OR REPLACE INTO parameters (key, value) VALUES (?, ?)");
  const updateMany = db.transaction((p) => {
    Object.entries(p).forEach(([k, v]) => stmt.run(k, parseFloat(v)));
  });
  updateMany(params);
}

function addTrader(name) {
  const exists = db.prepare("SELECT 1 FROM config WHERE category='Trader' AND value=?").get(name);
  if (!exists) db.prepare("INSERT INTO config (category, value) VALUES ('Trader', ?)").run(name);
}

function removeTrader(name) {
  db.prepare("DELETE FROM config WHERE category='Trader' AND value=?").run(name);
}

function addInstrument(name) {
  const exists = db.prepare("SELECT 1 FROM config WHERE category='Instrument' AND value=?").get(name);
  if (!exists) db.prepare("INSERT INTO config (category, value) VALUES ('Instrument', ?)").run(name);
}

function removeInstrument(name) {
  db.prepare("DELETE FROM config WHERE category='Instrument' AND value=?").run(name);
}

function generateTradeId() {
  const last = db.prepare("SELECT id FROM trades ORDER BY created_at DESC LIMIT 1").get();
  if (!last) return 'TR-001';
  const num = parseInt(last.id.split('-')[1]) + 1;
  return `TR-${String(num).padStart(3, '0')}`;
}

function generateReviewId() {
  const last = db.prepare("SELECT id FROM reviews ORDER BY reviewed_at DESC LIMIT 1").get();
  if (!last) return 'HV-001';
  const num = parseInt(last.id.split('-')[1]) + 1;
  return `HV-${String(num).padStart(3, '0')}`;
}

function createTrade(data) {
  const id = generateTradeId();
  const now = new Date().toISOString().split('T')[0];
  db.prepare(`
    INSERT INTO trades (id, created_at, submitted_by, instrument, timeframe, direction,
      trend_context, htf_confirmed, entry_type, idea_description, condition1, condition2, condition3, condition4, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Aktívny')
  `).run(id, now, data.submitted_by, data.instrument, data.timeframe, data.direction,
    data.trend_context, data.htf_confirmed, data.entry_type, data.idea_description,
    data.condition1 || null, data.condition2 || null, data.condition3 || null, data.condition4 || null);
  return id;
}

function getTrades() {
  const trades = db.prepare("SELECT * FROM trades ORDER BY created_at DESC").all();
  const params = getParameters();
  return trades.map(t => {
    const reviews = db.prepare("SELECT * FROM reviews WHERE trade_id = ?").all(t.id);
    const approved = reviews.filter(r => r.verdict === 'Schválené').length;
    const revision = reviews.filter(r => r.verdict === 'Na revíziu').length;
    const rejected = reviews.filter(r => r.verdict === 'Zamietnuté').length;
    let team_verdict = 'Bez hodnotenia';
    if (reviews.length > 0) {
      if (approved >= 2) team_verdict = 'Obchodovať';
      else if (approved >= 1 && revision >= 1 && rejected === 0) team_verdict = 'Na potvrdenie';
      else team_verdict = 'Neobchodovať';
    }
    const avg_entry = reviews.length ? reviews.reduce((s, r) => s + (r.proposed_entry || 0), 0) / reviews.length : null;
    const avg_sl = reviews.length ? reviews.reduce((s, r) => s + (r.proposed_sl || 0), 0) / reviews.length : null;
    const avg_tp = reviews.length ? reviews.reduce((s, r) => s + (r.proposed_tp || 0), 0) / reviews.length : null;
    const avg_rrr = reviews.filter(r => r.rrr).length ?
      reviews.filter(r => r.rrr).reduce((s, r) => s + r.rrr, 0) / reviews.filter(r => r.rrr).length : null;
    return { ...t, reviews, approved, revision, rejected, team_verdict,
      avg_entry, avg_sl, avg_tp, avg_rrr, review_count: reviews.length };
  });
}

function getTradeById(id) {
  const trade = db.prepare("SELECT * FROM trades WHERE id = ?").get(id);
  if (!trade) return null;
  const reviews = db.prepare("SELECT * FROM reviews WHERE trade_id = ? ORDER BY reviewed_at").all(id);
  const params = getParameters();
  const approved = reviews.filter(r => r.verdict === 'Schválené').length;
  const revision = reviews.filter(r => r.verdict === 'Na revíziu').length;
  const rejected = reviews.filter(r => r.verdict === 'Zamietnuté').length;
  let team_verdict = 'Bez hodnotenia';
  if (reviews.length > 0) {
    if (approved >= 2) team_verdict = 'Obchodovať';
    else if (approved >= 1 && revision >= 1 && rejected === 0) team_verdict = 'Na potvrdenie';
    else team_verdict = 'Neobchodovať';
  }
  return { ...trade, reviews, approved, revision, rejected, team_verdict };
}

function submitReview(tradeId, data) {
  const trade = db.prepare("SELECT * FROM trades WHERE id = ?").get(tradeId);
  if (!trade) throw new Error('Trade not found');
  const params = getParameters();
  const id = generateReviewId();
  const now = new Date().toISOString().split('T')[0];

  // Calculate RRR
  let rrr = null;
  if (data.proposed_entry && data.proposed_sl && data.proposed_tp) {
    const entry = parseFloat(data.proposed_entry);
    const sl = parseFloat(data.proposed_sl);
    const tp = parseFloat(data.proposed_tp);
    const risk = Math.abs(entry - sl);
    const reward = Math.abs(tp - entry);
    rrr = risk > 0 ? reward / risk : null;
  }

  const maxRisk = trade.trend_context === 'Do trendu' ? params.max_risk_trend : params.max_risk_counter;
  const riskInLimit = data.proposed_risk_pct ? (parseFloat(data.proposed_risk_pct) <= maxRisk ? 'Áno' : 'Nie') : null;
  const passedRRR = rrr ? (rrr >= params.min_rrr ? 'Áno' : 'Nie') : null;

  db.prepare(`
    INSERT OR REPLACE INTO reviews (id, trade_id, reviewed_at, reviewer, proposed_entry, proposed_sl, proposed_tp,
      rrr, fixed_plan, proposed_risk_pct, custom_condition1, custom_condition1_met,
      custom_condition2, custom_condition2_met, custom_condition3, custom_condition3_met, comment, verdict)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(id, tradeId, now, data.reviewer,
    data.proposed_entry ? parseFloat(data.proposed_entry) : null,
    data.proposed_sl ? parseFloat(data.proposed_sl) : null,
    data.proposed_tp ? parseFloat(data.proposed_tp) : null,
    rrr, data.fixed_plan || null,
    data.proposed_risk_pct ? parseFloat(data.proposed_risk_pct) : null,
    data.custom_condition1 || null, data.custom_condition1_met || null,
    data.custom_condition2 || null, data.custom_condition2_met || null,
    data.custom_condition3 || null, data.custom_condition3_met || null,
    data.comment || null, data.verdict);
  return id;
}

function getDashboard() {
  const total_trades = db.prepare("SELECT COUNT(*) as c FROM trades").get().c;
  const total_reviews = db.prepare("SELECT COUNT(*) as c FROM reviews").get().c;
  const approved_reviews = db.prepare("SELECT COUNT(*) as c FROM reviews WHERE verdict = 'Schválené'").get().c;
  const revision_reviews = db.prepare("SELECT COUNT(*) as c FROM reviews WHERE verdict = 'Na revíziu'").get().c;
  const rejected_reviews = db.prepare("SELECT COUNT(*) as c FROM reviews WHERE verdict = 'Zamietnuté'").get().c;
  const avg_rrr = db.prepare("SELECT AVG(rrr) as a FROM reviews WHERE rrr IS NOT NULL").get().a;
  const avg_risk = db.prepare("SELECT AVG(proposed_risk_pct) as a FROM reviews WHERE proposed_risk_pct IS NOT NULL").get().a;

  // Trade verdicts
  const trades = getTrades();
  const trade_to_go = trades.filter(t => t.team_verdict === 'Obchodovať').length;
  const trade_confirm = trades.filter(t => t.team_verdict === 'Na potvrdenie').length;
  const trade_no = trades.filter(t => t.team_verdict === 'Neobchodovať').length;
  const trade_none = trades.filter(t => t.team_verdict === 'Bez hodnotenia').length;

  return {
    total_trades, total_reviews, approved_reviews, revision_reviews, rejected_reviews,
    avg_rrr: avg_rrr ? Math.round(avg_rrr * 100) / 100 : 0,
    avg_risk: avg_risk ? Math.round(avg_risk * 100) / 100 : 0,
    trade_to_go, trade_confirm, trade_no, trade_none
  };
}

function updateTradeStatus(id, status) {
  db.prepare("UPDATE trades SET status = ? WHERE id = ?").run(status, id);
}

module.exports = {
  initDB, getTraders, getInstruments, getParameters, updateParameters,
  addTrader, removeTrader, addInstrument, removeInstrument,
  createTrade, getTrades, getTradeById, submitReview, getDashboard, updateTradeStatus
};
