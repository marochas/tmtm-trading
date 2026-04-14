# TMTM Trading Platform

Tímová webová platforma pre hodnotenie obchodných príležitostí.

## Požiadavky

- Python 3.8 alebo novší
- Žiadne externé knižnice (len štandardná knižnica Python)

## Spustenie

```bash
python3 server.py
```

Otvor prehliadač na: http://localhost:3000

## Zdieľanie s tímom

### Lokálna sieť (rovnaká WiFi / LAN)
Zisti svoju IP adresu:
```bash
# macOS / Linux
ip addr show | grep 'inet '
# Windows
ipconfig
```
Ostatní členovia tímu otvoria: `http://TVOJA_IP:3000`

### Vzdialený prístup cez ngrok (odporúčané)
1. Stiahnuť ngrok: https://ngrok.com/download
2. Spustiť: `ngrok http 3000`
3. Zdieľať URL s tímom (napr. `https://abc123.ngrok.io`)

### Zmena portu
```bash
PORT=8080 python3 server.py
```

## Štruktúra projektu

```
trading-platform/
├── server.py          # Backend server (Python, bez externých záv.)
├── trading.db         # SQLite databáza (vytvorí sa automaticky)
├── README.md
└── public/
    └── index.html     # React frontend (CDN, žiadny build)
```

## Ako to funguje

1. **Nový obchod** — jeden trader vytvorí obchodný nápad so všetkými parametrami
2. **Hodnotenie** — každý člen tímu klikne na obchod → "Moje hodnotenie" a vyplní:
   - Vlastný entry, SL, TP (RRR sa vypočíta automaticky)
   - Risk % z účtu
   - Vlastné podmienky + či sú splnené
   - Verdikt: Schválené / Na revíziu / Zamietnuté
3. **Tímové rozhodnutie** (automaticky):
   - ✅ **Obchodovať** — aspoň 2 schválenia
   - ⚠️ **Na potvrdenie** — aspoň 1 schválenie + 1 revízia, žiadne zamietnutie
   - ❌ **Neobchodovať** — tím sa nezhodol

## Nastavenia

V sekcii Nastavenia môžeš:
- Pridávať / odstraňovať obchodníkov
- Pridávať / odstraňovať instrumenty
- Meniť min. RRR, max. risk do trendu / proti trendu

## Databáza

Všetky dáta sú uložené v `trading.db` (SQLite). Zálohu urobíš jednoduchým skopírovaním tohto súboru.
