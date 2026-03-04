# Module Interfaces — Marketing Dronor

> This file is the **CONTRACT** between modules. Changes require agreement across all instances.
> Do NOT change without updating all consuming modules.

---

## M1 → M2: Profile data contract

M1 writes to `twitter_profiles` table. M2 reads from it.

**Key fields M2 depends on:**
```python
{
  "twitter_id": str,
  "username": str,
  "bio": str,
  "followers_count": int,
  "following_count": int,
  "tweets_count": int,
  # Enriched by profile_enricher:
  "professional_role": str,
  "industry": str,
  "tech_stack": list[str],     # JSONB
  "topics_of_interest": list[str],  # JSONB
  "primary_language": str,
  # Status:
  "outreach_status": str,  # pending | contacted | responded | converted
  "tier": str | None       # NULL until M2 fills it
}
```

---

## M2 → M3/M4: Classification result

M2 writes tier/category back to `twitter_profiles`.
M3 reads queue. M4 reads classification for message generation.

**Fields M2 writes:**
```python
{
  "tier": str,                    # S | A | B | C | D
  "category": str,                # one of 9 categories
  "identified_needs": list[dict], # JSONB [{need, context, url, urgency}]
  "dronor_use_cases": list[str],  # JSONB
  "assigned_expert_account": str  # account username
}
```

---

## M3 → M4: Account availability

M4 calls `account_state_manager` before generating messages.
**Contract:**
```python
# Input
{"account_id": str}
# Output  
{
  "can_send": bool,
  "state": str,         # warming | active | cooling | suspended
  "actions_remaining": int,
  "reset_at": str       # ISO datetime
}
```

---

## M4 → M5: Outreach task

M4 writes to `message_queue`. M5 operator reads from it.

**Queue record:**
```python
{
  "profile_id": int,           # FK to twitter_profiles
  "account_id": int,           # FK to twitter_accounts
  "message_text": str,         # final generated message
  "message_type": str,         # reply | quote | mention | dm
  "target_tweet_url": str,     # URL to reply to
  "priority": str,             # P0 | P1 | P2 | P3 | P4
  "tier": str,
  "category": str,
  "identified_need": str,      # 1-sentence context for operator
  "ab_variant": str,           # A | B | C
  "status": str                # pending | in_review | sent | rejected
}
```

---

## M5 → M6: Sent confirmation

M5 updates `message_queue.status = 'sent'` + writes to `outreach_sent`.
M6 polls for new sent records to start tracking.

**Trigger for M6:**
```sql
SELECT * FROM message_queue WHERE status = 'sent' AND tracked = FALSE
```

---

## M6 → M2/M3/M4: Feedback (weekly)

M6 writes to `feedback_log`:
```python
{
  "target_module": str,   # M2 | M3 | M4
  "feedback_type": str,   # response_rate | template_performance | account_health
  "data": dict,           # JSONB, module-specific
  "applied": bool
}
```

---

## Shared: PostgreSQL connection

```python
# infra/config.py (not committed, see config.example.py)
DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "marketing_dronor",
    "user": "...",
    "password": "..."
}
```

All modules import: `from infra.db import get_connection`
