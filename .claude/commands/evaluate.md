# Evaluate listings with Claude Code

You ARE the AI evaluator. Read unclassified listings from the database, evaluate each one against the buyer's criteria, and save your evaluations back.

## Buyer's criteria (from memory + CLAUDE.md)

- **Budget**: Target ~200,000 EUR, max 250,000 EUR
- **Type**: New build preferred (naujos statybos), but all construction types OK
- **Living area**: 100 kv.m preferred, acceptable 95–120 kv.m
- **Plot size**: 10 arų preferred, acceptable 8–12 arų
- **Location**: Kretinga city, Dupulčiai, Klonaičiai, Jakubavai, Jokūbavas, Raguviškiai, Kartena, Kiauleikiai, Girkaliai, Vydmantai
- **Preferences**: large plots (10+ arų), geothermal heating, avoid main road noise, prefer real photos over renders

## Steps

1. **Get unclassified listings**:
   ```bash
   docker compose exec backend python manage.py dump_unclassified --limit 10
   ```

2. **For each listing**, evaluate:
   - Does it pass hard filters (price, area, location)?
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
- **review** (0.4-0.8): Partial match, some deviations
- **skip** (0.0-0.4): Doesn't match criteria

## Example

```bash
docker compose exec backend python manage.py save_evaluation \
  --listing-id 50 --verdict review --score 0.6 \
  --summary "Namas Kretingoje, sena statyba bet gera kaina ir didelis sklypas. Reikia remonto."
```
