import json

from django.core.management.base import BaseCommand

from apps.classifier.models import ListingEvaluation
from apps.listings.models import Listing


class Command(BaseCommand):
    help = "Save a listing evaluation from Claude Code. Pass JSON via stdin or --data"

    def add_arguments(self, parser):
        parser.add_argument("--data", type=str, default="")
        parser.add_argument("--listing-id", type=int, default=0)
        parser.add_argument("--verdict", type=str, default="")
        parser.add_argument("--score", type=float, default=0.0)
        parser.add_argument("--summary", type=str, default="")

    def handle(self, *args, **options):
        if options["data"]:
            evaluations = json.loads(options["data"])
            if isinstance(evaluations, dict):
                evaluations = [evaluations]
        elif options["listing_id"]:
            evaluations = [
                {
                    "listing_id": options["listing_id"],
                    "verdict": options["verdict"],
                    "match_score": options["score"],
                    "summary": options["summary"],
                }
            ]
        else:
            raw = self.stdin.read()
            evaluations = json.loads(raw)
            if isinstance(evaluations, dict):
                evaluations = [evaluations]

        saved = 0
        for ev in evaluations:
            try:
                listing = Listing.objects.get(pk=ev["listing_id"])
            except Listing.DoesNotExist:
                self.stderr.write(f"Listing {ev['listing_id']} not found")
                continue

            ListingEvaluation.objects.update_or_create(
                listing=listing,
                defaults={
                    "verdict": ev.get("verdict", "review"),
                    "match_score": ev.get("match_score", 0.5),
                    "summary": ev.get("summary", ""),
                    "hard_filter_results": ev.get("hard_filter_results", {}),
                    "quality_notes": ev.get("quality_notes", []),
                    "red_flags": ev.get("red_flags", []),
                    "model_used": "claude-code",
                },
            )
            saved += 1
            self.stdout.write(
                f"[{ev.get('verdict')}] {ev.get('match_score', 0):.0%} — {listing.title[:50]}"
            )

        self.stdout.write(self.style.SUCCESS(f"Saved {saved} evaluations"))
