# TODOS

## Answer Open Question 1 on MES deletes (eng review D10)
- **What:** Does Cores Hub / Dies Hub hard-delete corrected rows?
- **Why:** D4 skipped deletion detection; that is only safe if the MES corrects by reversal.
- **Context:** if yes, add the nightly missing-ID sweep over the 7-day window (D4 option A); excluded rows kept for audit, reason "deleted in MES".
- **Depends on:** IT access to the MES schema.

## Create DESIGN.md and the tuk-tuk mark (design review D24)
- **What:** run /design-consultation to create DESIGN.md from the D17 tokens and design the tuk-tuk mark.
- **Why:** one source of truth for future screens and reviews. The D17 tokens in web/static/css/tokens.css are interim.

## Implement the real hub adapters (Open Question 1)
- ingest/adapters/cores.py and ingest/adapters/dies.py are stubs. Each needs the hub's engine, transaction table, MES ID, last-modified column, and whether quantity is good or gross.
- Add the eng review D8 test: an anonymized extract in a throwaway container of the hub's engine.
- Before production ingest, IT confirms a read replica / reporting copy, or indexes on the selection column and MES ID (eng review D9).

## Replace pilot local accounts with SSO/LDAP (Open Question 6)
- django-auth-ldap, matching IT's other plant tools, before rollout beyond the pilot.
