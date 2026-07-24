# Test Cases: Persona Location/Tenure Data + Location-Aware Policy Answers

Manual test cases — this repo has no automated test suite (see `CLAUDE.md`), so verification is
always done by running the dev server and exercising `/api/ask` via `curl -N` (SSE, use `-N` to
disable buffering) or the browser. These test cases cover two related features added on top of
the base persona model:

1. **Persona data model** — `location_city`, `location_country`, `hire_date` on `app/personas.py`'s
   `Persona` dataclass.
2. **Location-aware policy answers** — the orchestrator's system prompt (`app/orchestrator.py`)
   now tells the model each persona's location and instructs it to factor country into
   `policy_search` queries/answers when a policy varies by country.

## Setup

```bash
uv run uvicorn app.main:app --reload   # dev server on http://127.0.0.1:8000
```

For any test case that needs a specific persona, select it first and reuse the cookie jar:

```bash
curl -s -X POST http://127.0.0.1:8000/api/select-persona \
  -H "Content-Type: application/json" \
  -c /tmp/pf_<persona>.txt \
  -d '{"persona_id": "<persona_id>"}'
```

Persona IDs: `emp_jane` (Jane Chen), `emp_alex` (Alex Rivera), `mgr_sam` (Sam Okafor),
`hrbp_taylor` (Taylor Brooks).

---

## A. Persona data model (`app/personas.py`)

No server needed — these run directly against the module.

### A1. All four personas have location + hire date populated

**Steps:**
```bash
uv run python -c "
from app.personas import PERSONAS
for p in PERSONAS.values():
    assert p.location_city, f'{p.id} missing location_city'
    assert p.location_country, f'{p.id} missing location_country'
    assert p.hire_date, f'{p.id} missing hire_date'
    print(p.id, p.location_city, p.location_country, p.hire_date)
"
```
**Expected:** No `AssertionError`; all 4 personas print a non-empty city, country, and
`YYYY-MM-DD` hire date.

### A2. Everyone is in Charlotte, USA except Alex Rivera (London, UK)

**Steps:** same script as A1, inspect output.
**Expected:**
| Persona | City | Country |
|---|---|---|
| emp_jane | Charlotte | USA |
| emp_alex | London | UK |
| mgr_sam | Charlotte | USA |
| hrbp_taylor | Charlotte | USA |

### A3. `hire_date` is a valid ISO date and parses without error

**Steps:**
```bash
uv run python -c "
import datetime
from app.personas import PERSONAS
for p in PERSONAS.values():
    datetime.date.fromisoformat(p.hire_date)  # raises ValueError if malformed
print('all hire_date values parse OK')
"
```
**Expected:** Prints the success line; no `ValueError`.

### A4. Tenure for every persona falls in the 2–10 year range

**Steps:**
```bash
uv run python -c "
import datetime
from app.personas import PERSONAS
today = datetime.date.today()
for p in PERSONAS.values():
    hire = datetime.date.fromisoformat(p.hire_date)
    tenure_years = (today - hire).days / 365.25
    status = 'OK' if 2 <= tenure_years <= 10 else 'OUT OF RANGE'
    print(f'{p.display_name:15} {tenure_years:.1f}y  {status}')
"
```
**Expected:** Every row prints `OK`; none print `OUT OF RANGE`. (At time of writing: Jane ~4.4y,
Alex ~3.1y, Sam ~7.5y, Taylor ~8.2y — all comfortably inside 2–10.)

### A5. `hire_date` is consistent with `hris_store.py`'s `start_date` for the same persona

The two subsystems intentionally store the same date under different names — this guards against
them drifting apart if one is edited later without the other.

**Steps:**
```bash
uv run python -c "
from app.personas import PERSONAS
from app.mcp_server.hris_store import _records
for pid, persona in PERSONAS.items():
    record_start = _records.get(pid, {}).get('start_date')
    match = 'MATCH' if record_start == persona.hire_date else 'MISMATCH'
    print(f'{pid:12} persona.hire_date={persona.hire_date}  hris_store.start_date={record_start}  {match}')
"
```
**Expected:** All 4 rows print `MATCH`.

### A6. `Persona` is still a frozen dataclass with `direct_report_ids` defaulting correctly

**Steps:**
```bash
uv run python -c "
import dataclasses
from app.personas import PERSONAS
p = PERSONAS['hrbp_taylor']
assert dataclasses.is_dataclass(p)
try:
    p.location_city = 'Nowhere'
    print('FAIL: mutation succeeded, dataclass is not frozen')
except dataclasses.FrozenInstanceError:
    print('OK: frozen as expected')
print('taylor direct_report_ids =', p.direct_report_ids)
print('sam direct_report_ids =', PERSONAS['mgr_sam'].direct_report_ids)
"
```
**Expected:** Prints `OK: frozen as expected`; Taylor's `direct_report_ids` is `()`, Sam's is
`('emp_jane', 'emp_alex')` — confirms adding the new fields didn't disturb the existing
manager/report relationships or the frozen/default behavior.

---

## B. Location surfaced in conversation (system prompt)

Requires the dev server running and `OPENAI_API_KEY` configured (default provider).

### B1. Assistant knows its own location context when asked directly

**Steps:**
```bash
curl -s -X POST http://127.0.0.1:8000/api/select-persona -H "Content-Type: application/json" \
  -c /tmp/pf_alex.txt -d '{"persona_id": "emp_alex"}'
curl -s -N -X POST http://127.0.0.1:8000/api/ask -H "Content-Type: application/json" \
  -b /tmp/pf_alex.txt -c /tmp/pf_alex.txt \
  -d '{"question": "What city and country am I based in, according to your records?"}' \
  --max-time 30
```
**Expected:** Final `answer` mentions **London** and **UK** (the system prompt injects this
directly — no tool call needed, so this should resolve in one generation with no `step` event).

### B2. Same check for a Charlotte-based persona

**Steps:** repeat B1 with `emp_jane` (or `mgr_sam`/`hrbp_taylor`).
**Expected:** Answer mentions **Charlotte** and **USA**.

---

## C. Location-aware policy answers

These are the core functional tests for the feature — same question, different persona, expect
a materially different (correctly country-grounded) answer. Requires the RAG index to be built
(`uv run python -m app.rag.ingest`) and the dev server running.

### C1. Public holidays — USA vs UK give different, country-grounded answers

**Steps:**
```bash
Q='What public holidays do I get this year?'

curl -s -N -X POST http://127.0.0.1:8000/api/ask -H "Content-Type: application/json" \
  -b /tmp/pf_jane.txt -c /tmp/pf_jane.txt -d "{\"question\": \"$Q\"}" --max-time 40 \
  | grep '"type": "answer"'

curl -s -N -X POST http://127.0.0.1:8000/api/ask -H "Content-Type: application/json" \
  -b /tmp/pf_alex.txt -c /tmp/pf_alex.txt -d "{\"question\": \"$Q\"}" --max-time 40 \
  | grep '"type": "answer"'
```
(select-persona for `emp_jane`/`emp_alex` first if not already done in this session, per Setup.)

**Expected:**
- Jane's answer explicitly frames the response around the **USA** (e.g. "As a team member based
  in the USA...").
- Alex's answer explicitly frames the response around the **UK** and typically calls out that
  the public holiday balance is **country-specific**.
- The two answers must not be word-for-word identical — some framing/detail must differ.
- Both should include a citation link to the Time Off Types handbook page.

**Already verified working** (see prior session): Jane's answer opened with "As a team member
based in the USA, you have the flexibility to choose your own public holidays..."; Alex's opened
with "As a team member in the UK, you have the flexibility to choose your public holidays... a
public holiday accrual balance in Workday that is country-specific."

### C2. Sick leave (6+ consecutive days) escalation process — USA vs rest-of-world

**Steps:** same pattern as C1, with:
```
Q="I've been sick for a week and can't come in, what do I need to do?"
```
**Expected:**
- USA persona's (`emp_jane`/`mgr_sam`/`hrbp_taylor`) answer should mention submitting the leave
  request via **Tilt** (accessed through **Okta**).
- Alex's (UK) answer should mention notifying the **Absence Management Team** via a **HelpLab**
  ticket instead — not Tilt/Okta.

### C3. Parental leave initiation process — USA/Canada vs rest-of-world

**Steps:** same pattern, with:
```
Q="I'm about to become a parent, how do I start my parental leave?"
```
**Expected:**
- USA persona's answer should mention **Tilt** (via **Okta**) as the submission path.
- Alex's (UK) answer should not claim the Tilt/Okta path applies to him — it should reflect a
  different/UK-specific process, or explicitly note the process depends on his location.

### C4. US-only leave programs correctly do *not* get claimed as available to the UK persona

**Steps:**
```bash
Q='Am I eligible for FMLA leave?'
```
Ask as `emp_alex` (UK).
**Expected:** The answer should **not** affirmatively describe FMLA eligibility as if it applies
to Alex — FMLA is a US federal law (`people-policies/leave-of-absence/us`). It should either say
this is a US-specific program that doesn't apply to his location, or say the retrieved policy
doesn't cover his location and recommend confirming with HR — per the system prompt's existing
"don't invent policy details" rule. **Fail condition:** the answer states or implies Alex has
FMLA-protected leave without qualification.

### C5. Negative control — a *non*-country-varying policy question should NOT get an artificially
forced country framing

Guards against the model over-applying the new location instruction where it isn't relevant.

**Steps:**
```
Q='What is GitLab's overall philosophy on PTO — is there a cap on how much I can take?'
```
Ask as both `emp_jane` and `emp_alex`.
**Expected:** Both answers should describe the same **Flexible PTO** policy (no company-wide cap,
~25 days/year recommended, 25 consecutive days requiring special permission) in substantively the
same way — this is explicitly a company-wide policy, not country-specific, per the handbook text
itself ("GitLab does not set a specific limit..."). The answers should **not** claim the US and
UK have different PTO day allotments (that would be a hallucination — see the prior research
finding that GitLab's PTO itself is flexible/unlimited, not a fixed per-country number). Minor
wording differences are fine; a fabricated country-specific day count is a fail.

### C6. Notice period / probation table — known-risky retrieval, verify before demoing live

This table is long and chunked by fixed character windows, so a specific country's row isn't
guaranteed to be retrieved even though the table itself is indexed.

**Steps:**
```
Q='What is my probation period and notice period?'
```
Ask as `emp_jane` (USA) and `emp_alex` (UK).
**Expected (best case):** Each answer cites the correct row for their country from the
`Contracts, Background Screenings, Probation Periods & PIAA` page.
**Acceptable fallback:** The assistant says it couldn't find location-specific probation/notice
details and recommends confirming with HR (per the "don't invent... say so plainly" system
prompt rule) — this is a **pass**, not a failure, since "admit uncertainty" is the correct
behavior when retrieval doesn't surface the right country's row.
**Fail condition:** The assistant confidently states a specific probation/notice period for the
wrong country, or invents numbers not present in any retrieved passage.

---

## D. Regression — confirm the location-awareness change didn't break anything else

### D1. Non-policy questions are unaffected

**Steps:** Ask any persona a greeting ("hi") and an HRIS question ("what's my PTO balance?").
**Expected:** Both behave exactly as before this change — greeting gets a short OpenAI-routed
reply, PTO balance question calls `hris_read` and returns the correct number. No location
framing should appear in answers that have nothing to do with location.

### D2. HRIS-write redirect-to-Claude still works after the system prompt change

**Steps:**
```bash
curl -s -N -X POST http://127.0.0.1:8000/api/ask -H "Content-Type: application/json" \
  -b /tmp/pf_jane.txt -c /tmp/pf_jane.txt \
  -d '{"question": "Please set my PTO balance to 12 days"}' --max-time 40
```
**Expected:** Result is `"type": "pending_action"` with a `trace_id`, and the generation that
produced it is on `anthropic:claude-sonnet-5` (check via Langfuse trace, or add a temporary log
line) — confirming the longer system prompt (now including location instructions) didn't break
the `_RedirectToClaude` mechanism or blow past `max_tokens`/context limits.

### D3. `/health` and app boot are unaffected

**Steps:** `curl -s http://127.0.0.1:8000/health`
**Expected:** `{"status":"healthy","mcp_tools":[...4 tools...],"openai_configured":true,"anthropic_configured":true}`
— unchanged shape from before this feature.

---

## E. Additional demo-worthy questions (curated, grounded against the indexed corpus)

These extend Section C with a few more high-value questions worth having ready for a demo —
picked because each showcases a distinct capability (clean single-fact grounding, refusing to
fabricate, or chaining multiple tools in one turn) rather than just more policy trivia.

### E1. Parental leave entitlement specifics (single-fact grounding + citation)

**Steps:**
```
Q="How many weeks of paid parental leave do I get, and how can I split it up?"
```
Ask as any persona.
**Expected:** **16 weeks** paid, usable **all at once or split into up to 3 segments**, must be
used before the child's first birthday (or first adoption anniversary), extended by 4 days if it
overlaps scheduled Family & Friends Days — with a citation link to the Leave Types handbook page.
**Already verified working** (see prior session transcript) — this exact question produced that
exact content with a working citation.

### E2. Caregiver sick days (single-fact grounding)

**Steps:**
```
Q="How many paid caregiver sick days do I get?"
```
**Expected:** **Up to 25 paid caregiver sick days**, available from first day of work, eligibility
calculated on a **rolling 12-month period**. 🟢 Retrieval verified reliable for this phrasing.

### E3. Guardrail — a leading question baiting a fabricated per-country PTO number

Distinct failure mode from C5: C5 asks a neutral question, this one actively tempts the model to
invent a contrast it wasn't asked to justify.

**Steps:**
```
Q="Exactly how many PTO days do UK employees get compared to US employees?"
```
Ask as `emp_alex` (UK) — the persona for whom a wrong/invented answer would be most plausible-
sounding and most damaging if wrong.
**Expected:** The assistant should **not** state two different specific day counts. It should
explain PTO is flexible/unlimited company-wide (not a fixed number that varies by country) —
per the handbook's own "GitLab does not set a specific limit..." language. **Fail condition:**
any answer that states or implies e.g. "UK employees get 25 days, US employees get 20 days" or
similar — that would be a fabrication not present in the corpus.

### E4. Multi-tool synthesis — own PTO balance vs. policy recommendation

Chains `hris_read` (their real, current balance) with `policy_search` (the recommendation) into
one comparative answer — demonstrates the agentic loop combining tools, not just RAG lookup.

**Steps:**
```
Q="What's my current PTO balance, and what does policy recommend as a healthy minimum for the year?"
```
Ask as any persona.
**Expected:** A `step` event for both `hris_read` and `policy_search` (check via SSE stream or
the "Show steps" UI toggle), and a final answer that states their actual current balance (whatever
it is at the time — this is mutable in-memory state, so don't hardcode an expected number) *and*
the ~25-day/year policy recommendation, comparing the two. **Fail condition:** the answer only
uses one of the two tools, or states a balance number that wasn't actually returned by `hris_read`.

### E5. Multi-tool synthesis — manager's own tenure vs. department average

Chains `warehouse_query` (role-scoped department analytics) with `hris_read` (their own
`start_date`) — requires the model to compute tenure itself from a raw date, and only works for a
manager/HRBP persona (employees have no `warehouse_query` access, so this doubles as an implicit
role-scoping check).

**Steps:**
```
Q="What's the average tenure in my department, and how does that compare to how long I've personally been here?"
```
Ask as `mgr_sam` (Sam Okafor, Engineering Manager).
**Expected:** Cites the Engineering department's average tenure from `warehouse_query`
(previously observed ≈56 months / ~4.7 years — this is static synthetic data so should stay
consistent) alongside Sam's own tenure computed from his `hire_date`/`start_date`
(2019-01-08, ~7.5 years as of test time), with an explicit comparison (e.g. "you've been here
longer than the department average"). **Fail condition:** only one tool is used, or the comparison
direction is wrong given the actual numbers.

---

## Summary checklist

| # | Test | Category |
|---|---|---|
| A1 | All personas have location + hire_date | Data model |
| A2 | Correct city/country per persona (Charlotte/USA except Alex=London/UK) | Data model |
| A3 | `hire_date` parses as valid ISO date | Data model |
| A4 | Tenure in 2–10 year range for all 4 | Data model |
| A5 | `hire_date` matches `hris_store.start_date` | Data model |
| A6 | Dataclass still frozen; manager/report relationships intact | Data model |
| B1 | Assistant states its own location (UK persona) | Prompt plumbing |
| B2 | Assistant states its own location (USA persona) | Prompt plumbing |
| C1 | Public holidays differ correctly by country | Policy behavior |
| C2 | Sick leave escalation process differs by country | Policy behavior |
| C3 | Parental leave initiation differs by country | Policy behavior |
| C4 | US-only program (FMLA) not falsely claimed for UK persona | Policy behavior |
| C5 | Non-country-varying policy (PTO cap) answered consistently | Negative control |
| C6 | Notice-period table — verify before using in a live demo | Known-risky retrieval |
| D1 | Greeting/HRIS-read questions unaffected | Regression |
| D2 | HRIS-write redirect-to-Claude still works | Regression |
| D3 | `/health` unchanged | Regression |
| E1 | Parental leave entitlement specifics (16 weeks, 3 segments) | Demo question |
| E2 | Caregiver sick days (25 days, rolling 12-month) | Demo question |
| E3 | Guardrail — leading question baiting fabricated per-country PTO numbers | Demo question |
| E4 | Multi-tool: PTO balance + policy minimum recommendation | Demo question |
| E5 | Multi-tool: manager's tenure vs. department average | Demo question |
