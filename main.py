import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.config import DEFAULT_SHEET_URL, SETLISTFM_USER, BASE_DIR
from src.csv_parser import parse_concerts_source
from src.setlist_api import SetlistFMClient
from src.gap_analysis import reconcile_history
from src.analytics import ConcertAnalytics
from src.album_enricher import AlbumEnricher
from src.reporter import render_html_report

def main():
    parser = argparse.ArgumentParser(description="Live Concert & Setlist.fm Analytics Dashboard")
    parser.add_argument("--source", default=DEFAULT_SHEET_URL, help="Path to CSV or public Google Sheets URL")
    parser.add_argument("--user", default=SETLISTFM_USER, help="Setlist.fm username")
    parser.add_argument("--no-cache", action="store_true", help="Force fresh API fetch bypassing local caches")
    parser.add_argument("--refresh-setlists", action="store_true", help="Force fresh fetch of Setlist.fm attended shows")
    parser.add_argument("--refresh-unresolved", action="store_true", help="Re-query MusicBrainz for songs currently marked Non-Album / Singles")
    parser.add_argument("--refresh-all", action="store_true", help="Force re-query of all songs in catalog")
    parser.add_argument("--output", default="concert_report.html", help="HTML report output path")
    args = parser.parse_args()

    print(f"\n[1/5] 📄 Loading concert data from: {args.source[:50]}...")
    csv_records = parse_concerts_source(args.source)
    print(f"      Parsed {len(csv_records)} concert entries.")

    print(f"\n[2/5] 🌐 Fetching Setlist.fm attended history for '@{args.user}'...")
    client = SetlistFMClient()
    user_attended = client.get_user_attended(
        args.user,
        use_cache=not (args.no_cache or args.refresh_all or args.refresh_setlists)
    )
    print(f"      Retrieved {len(user_attended)} attended shows from Setlist.fm.")

    print(f"\n[3/5] 🔍 Running Bidirectional Gap Analysis with Global Setlist Detection...")
    gap_results = reconcile_history(csv_records, user_attended, client=client)
    print(f"      Matched: {gap_results['matched_count']} / {gap_results['total_csv']} ({gap_results['coverage_percentage']}%)")
    print(f"      Shows with Missing Tags: {len(gap_results['csv_missing_or_partial'])}")
    print(f"      On Setlist.fm but not in Spreadsheet: {len(gap_results['setlist_only'])}")

    print(f"\n[4/5] 📊 Computing Concert & Setlist Metrics & Album Metadata...")
    analytics = ConcertAnalytics(gap_results["matched"], csv_records)
    stats = analytics.compute_all_metrics()
    print(f"      Songs Analyzed: {stats['total_songs_heard']} total ({stats['unique_songs_heard']} unique)")

    # Enrich all songs in the catalog with live progress & caching
    enricher = AlbumEnricher()
    album_enrichments = enricher.enrich_catalog(
        stats["all_songs_list"],
        refresh_unresolved=args.refresh_unresolved,
        refresh_all=(args.refresh_all or args.no_cache)
    )

    # Compute reverse-chronological concert drilldown, venue geo map & musician tenure tracking
    from src.venue_mapper import generate_venue_map_data
    from src.musician_tracker import analyze_musicians_live
    stats["concerts_drilldown"] = analytics.compute_concert_drilldown(album_enrichments)
    stats["venue_map"] = generate_venue_map_data(csv_records, gap_results["matched"])
    stats["musicians"] = analyze_musicians_live(csv_records)

    print(f"\n[5/5] 🎨 Rendering Interactive Dashboard Report...")
    template_dir = BASE_DIR / "src" / "templates"
    output_path = BASE_DIR / args.output
    render_html_report(template_dir, output_path, args.user, gap_results, stats, album_enrichments)
    print(f"      ✅ Report successfully generated at: {output_path}\n")

if __name__ == "__main__":
    main()
