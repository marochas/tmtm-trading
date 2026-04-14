const express = require('express');
const cors = require('cors');
const path = require('path');
const db = require('./db');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Initialize DB
db.initDB();

// ─── CONFIG ────────────────────────────────────────────────────────────────

app.get('/api/config', (req, res) => {
  res.json({
    traders: db.getTraders(),
    instruments: db.getInstruments(),
    parameters: db.getParameters()
  });
});

app.put('/api/config/parameters', (req, res) => {
  try {
    db.updateParameters(req.body);
    res.json({ ok: true });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.post('/api/config/traders', (req, res) => {
  try {
    db.addTrader(req.body.name);
    res.json({ ok: true });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.delete('/api/config/traders/:name', (req, res) => {
  db.removeTrader(req.params.name);
  res.json({ ok: true });
});

app.post('/api/config/instruments', (req, res) => {
  try {
    db.addInstrument(req.body.name);
    res.json({ ok: true });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.delete('/api/config/instruments/:name', (req, res) => {
  db.removeInstrument(req.params.name);
  res.json({ ok: true });
});

// ─── DASHBOARD ─────────────────────────────────────────────────────────────

app.get('/api/dashboard', (req, res) => {
  res.json(db.getDashboard());
});

// ─── TRADES ────────────────────────────────────────────────────────────────

app.get('/api/trades', (req, res) => {
  res.json(db.getTrades());
});

app.get('/api/trades/:id', (req, res) => {
  const trade = db.getTradeById(req.params.id);
  if (!trade) return res.status(404).json({ error: 'Trade not found' });
  res.json(trade);
});

app.post('/api/trades', (req, res) => {
  try {
    const id = db.createTrade(req.body);
    res.status(201).json({ id });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

app.patch('/api/trades/:id/status', (req, res) => {
  try {
    db.updateTradeStatus(req.params.id, req.body.status);
    res.json({ ok: true });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

// ─── REVIEWS ───────────────────────────────────────────────────────────────

app.post('/api/trades/:id/reviews', (req, res) => {
  try {
    const reviewId = db.submitReview(req.params.id, req.body);
    res.status(201).json({ id: reviewId });
  } catch (e) {
    res.status(400).json({ error: e.message });
  }
});

// ─── SPA FALLBACK ──────────────────────────────────────────────────────────

app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, () => {
  console.log(`\n🚀 Trading Platform beží na http://localhost:${PORT}\n`);
  console.log('   Zdieľaj s tímom cez lokálnu sieť alebo ngrok.\n');
});
