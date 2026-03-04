# Marketing Dronor

Twitter marketing automation system for Dronor/Extella — 6-module pipeline from data collection to conversion tracking.

## Architecture

```
M1 Data Collector → M2 Profile Analyzer → M3 Account Manager
                                        ↘ M4 Message Generator → M5 Operator Interface → M6 Response Tracker
```

## Modules

| Module | Description | Sprint | Instance |
|--------|-------------|--------|----------|
| M1 | Data Collector — 200K Twitter profiles | 1 | A |
| M2 | Profile Analyzer — tier/category classification | 2 | B |
| M3 | Account Manager — 56 accounts fleet | 2 | C |
| M4 | Message Generator — personalized outreach | 2 | B |
| M5 | Operator Interface — human-in-the-loop | 3 | C |
| M6 | Response Tracker — replies & conversions | 3 | C |

## Development

### Branches
- `main` — stable, protected
- `feat/m1-data-collector` → Instance A
- `feat/m2-m4-intelligence` → Instance B  
- `feat/m3-m5-m6-operations` → Instance C

### Linear
Project: **Marketing Dronor** (MKT team)
https://linear.app/dronor

### Setup
```bash
pip install tweepy anthropic psycopg2-binary playwright
cp infra/config.example.py infra/config.py  # fill Twitter/DB credentials
psql -f infra/db/schema.sql
```

## Status
- [ ] Sprint 1: Foundation & M1 (2026-03-04 → 2026-03-18)
- [ ] Sprint 2: M2/M3/M4 Core (2026-03-18 → 2026-04-01)
- [ ] Sprint 3: M5/M6 + Integration (2026-04-01 → 2026-04-15)
