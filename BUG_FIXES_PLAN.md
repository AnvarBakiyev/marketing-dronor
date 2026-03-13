# Marketing Dronor - Plan Ispravleniya Oshibok

**Data sozdaniya:** 13 marta 2026  
**Status:** V processe

---

## SVODKA NAYDENNYH OSHIBOK

| #  | Prioritet    | Modul | Oshibka | Status |
|----|-------------|--------|--------|--------|
| 1  | KRITICHNO   | config.py | Net DATABASE_URL dlya Railway | TODO |
| 2  | KRITICHNO   | profile_enricher.py | Model Claude ustarela | TODO |
| 3  | KRITICHNO   | message_generator.py | Model Claude ustarela | TODO |
| 4  | KRITICHNO   | wave_classifier.py | Model Claude ustarela | TODO |
| 5  | KRITICHNO   | needs_analyzer.py | Model Claude ustarela | TODO |
| 6  | SREDNE      | .env | Otsutstvuet DATABASE_URL | TODO |
| 7  | SREDNE      | tests/*.py | Unicode oshibki (-> simvoly) | TODO |
| 8  | NIZKO       | config.py | Net OPENAI_API_KEY | TODO |

---

## ETAP 1: KRITICHESKIE ISPRAVLENIYA (BD + AI)

### 1.1 Ispravlenie infra/config.py

**Problema:** Otsutstvuet DATABASE_URL dlya deploya na Railway

**Fayl:** infra/config.py

**Tekushchiy kod (stroki 7-30):**
```python
# --- Database ---
_DATABASE_URL = os.environ.get('DATABASE_URL', '')

if _DATABASE_URL:
    import urllib.parse
    _u = urllib.parse.urlparse(_DATABASE_URL)
    DB_CONFIG = {
        'host': _u.hostname,
        'port': _u.port or 5432,
        'dbname': _u.path.lstrip('/'),
        'user': _u.username,
        'password': _u.password,
    }
else:
    # Local fallback
    DB_CONFIG = {
        'host': os.environ.get('DB_HOST', 'localhost'),
        'port': int(os.environ.get('DB_PORT', 5432)),
        'dbname': os.environ.get('DB_NAME', 'marketing_dronor'),
        'user': os.environ.get('DB_USER', 'postgres'),
        'password': os.environ.get('DB_PASSWORD', ''),
    }
```

**Status:** [OK] Kod korekten! DATABASE_URL uzhe podderzhivaetsya.

**Deystvie:** Dobavit DATABASE_URL v Railway Variables

---

### 1.2 Ispravlenie modeley Claude

**Problema:** Ispolzuetsya ustarevshaya model claude-haiku-4-5-20251001

**Aktualnye modeli Anthropic (mart 2026):**
- claude-3-haiku-20240307 - bystraya i deshevaya
- claude-3-5-sonnet-20241022 - balans skorosti i kachestva
- claude-3-opus-20240229 - maksimalnoe kachestvo

**Rekomendaciya:** Ispolzovat claude-3-haiku-20240307 dlya zadach klassifikacii

#### Fayly dlya ispravleniya:

**1. m1_data_collector/profile_enricher.py (stroka 76)**
```python
# BYLO:
model="claude-haiku-4-5-20251001",

# STALO:
model="claude-3-haiku-20240307",
```

**2. m4_message_generator/message_generator.py (stroka 119)**
```python
# BYLO:
model="claude-haiku-4-5-20251001",

# STALO:
model="claude-3-haiku-20240307",
```

**3. m2_profile_analyzer/wave_classifier.py (stroka 65)**
```python
# BYLO:
model="claude-haiku-4-5-20251001",

# STALO:
model="claude-3-haiku-20240307",
```

**4. m2_profile_analyzer/needs_analyzer.py (stroka 91)**
```python
# BYLO:
model="claude-haiku-4-5-20251001",

# STALO:
model="claude-3-haiku-20240307",
```

---

### 1.3 Dobavlenie DATABASE_URL v .env

**Fayl:** .env

**Dobavit stroku:**
```
DATABASE_URL=postgresql://postgres:HztyVVdxZXYcFesSzVeTnNkGpBDyMEVP@mainline.proxy.rlwy.net:39885/railway
```

---

## ETAP 2: SREDNIE ISPRAVLENIYA (Testy)

### 2.1 Unicode oshibki v testah

**Problema:** Simvoly vyzyvayut oshibku kodirovki

**Fayly:**
- tests/test_e2e_sprint4.py
- Drugie testovye fayly

**Reshenie:** Zamenit strelki na -> ili dobavit # -*- coding: utf-8 -*-

---

## ETAP 3: NIZKOPRIORITETNYE ISPRAVLENIYA

### 3.1 Dobavlenie OPENAI_API_KEY

**Problema:** Esli ponadobitsya OpenAI dlya kakih-to zadach

**Fayl:** infra/config.py

**Dobavit:**
```python
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
```

---

## PROVERKA API KLYUCHEY

### Tekushchie klyuchi v .env:

| Klyuch | Znachenie (maskirovano) | Status |
|------|------------------------|--------|
| ANTHROPIC_API_KEY | sk-ant-api03-...GKYQ-GNnKGAAA | [OK] Format vernyy |
| TWITTERAPI_IO_KEY | new1_0489a7ef... | [OK] Format vernyy |
| GOLOGIN_API | eyJhbGciOiJI... (JWT) | [OK] Format vernyy |
| DATABASE_URL | [X] Otsutstvuet | [TODO] Nuzhno dobavit |
| OPENAI_API_KEY | [X] Otsutstvuet | [?] Opcionalno |

### Ozhidaemyy format klyuchey:

| Servis | Format | Primer |
|--------|--------|--------|
| Anthropic | sk-ant-api03-... | [OK] Sootvetstvuet |
| TwitterAPI.io | alphanumeric + underscore | [OK] Sootvetstvuet |
| GoLogin | JWT token | [OK] Sootvetstvuet |
| Railway Postgres | postgresql://user:pass@host:port/db | Nuzhen |
| OpenAI | sk-proj-... ili sk-... | Opcionalno |

---

## PORYADOK VYPOLNENIYA

### Shag 1: Ispravit modeli Claude (4 fayla)
```bash
# Zapustit skript ili vruchnuyu zamenit:
claude-haiku-4-5-20251001 -> claude-3-haiku-20240307
```

### Shag 2: Dobavit DATABASE_URL v .env
```bash
echo "DATABASE_URL=postgresql://postgres:HztyVVdxZXYcFesSzVeTnNkGpBDyMEVP@mainline.proxy.rlwy.net:39885/railway" >> .env
```

### Shag 3: Dobavit DATABASE_URL v Railway Variables
1. Otkryt Railway Dashboard
2. Vybrat servis
3. Variables -> Add Variable
4. Name: DATABASE_URL
5. Value: (skopirovat iz shaga 2)

### Shag 4: Ispravit testy (Unicode)
```python
# Dobavit v nachalo faylov:
# -*- coding: utf-8 -*-
```

### Shag 5: Proverit rabotu
```bash
python -c "from infra.db import get_connection; print('DB OK')"
python -c "from m1_data_collector.profile_enricher import profile_enricher; print('Enricher OK')"
```

---

## CHECKLIST PERED DEPLOEM

- [ ] Modeli Claude ispravleny (4 fayla)
- [ ] DATABASE_URL dobavlen v .env
- [ ] DATABASE_URL dobavlen v Railway Variables
- [ ] ANTHROPIC_API_KEY proveren i rabotaet
- [ ] TWITTERAPI_IO_KEY proveren
- [ ] GOLOGIN_API proveren
- [ ] Testy Unicode ispravleny
- [ ] Lokalnyy test podklyucheniya k BD uspeshen
- [ ] Test enricher uspeshen

---

## PRIMECHANIYA

1. **AWS ne trebuetsya** - proekt ispolzuet Railway PostgreSQL
2. **OpenAI opcionalno** - vse AI-zadachi rabotayut na Anthropic Claude
3. **OPENAI_API_KEY nuzhen tolko dlya e2e testov** - esli oni ispolzuyut OpenAI napryamuyu

---

*Dokument sozdan avtomaticheski agentom Dronor*
