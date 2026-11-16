# Evaluate listings with Claude Code

You ARE the AI evaluator. Read unclassified listings from the database, evaluate each one against the buyer's criteria, and save your evaluations back.

## Buyer's criteria

The criteria live in one place, `backend/apps/classifier/criteria.py` (the human-readable source is `HOUSE_CRITERIA.md`). Do not work from memory or from a copy: print them first and apply exactly what it prints.

```bash
docker compose exec backend python -m apps.classifier.criteria
```

A value missing from a listing is **unknown, not a fail**. Only a known value outside a limit is a hard fail.

## Steps

1. **Print the criteria** (above), then **get unclassified listings**:
   ```bash
   docker compose exec backend python manage.py dump_unclassified --limit 10
   ```

2. **For each listing**, evaluate:
   - Hard filters (price, area, plot, location, new build, part of a house), in that order. Stop at the first hard fail: verdict `skip`, score 0.0-0.2.
   - Quality check (photos, description, cadastral number)
   - Red flags (too-good price, vague, missing details)
   - Overall match score 0.0-1.0

3. **Save each evaluation**:
   ```bash
   docker compose exec backend python manage.py save_evaluation \
     --listing-id {ID} --verdict {match|review|skip} --score {0.0-1.0} \
     --summary "Your evaluation in Lithuanian"
   ```

## Verdict guide

- **match** (0.8-1.0): Meets all criteria, worth viewing
- **review** (0.4-0.8): Nothing fails, but something is unknown or only partly fits
- **skip** (0.0-0.2): A hard filter fails

## Example

```bash
docker compose exec backend python manage.py save_evaluation \
  --listing-id 50 --verdict review --score 0.6 \
  --summary "Naujas namas, kaina ir plotas tinka, sklypo dydis nenurodytas. Reikia patikslinti."
```
