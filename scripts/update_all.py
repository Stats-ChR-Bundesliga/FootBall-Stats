"""
update_all.py

Το κεντρικό script που τρέχει αυτόματα (μέσω GitHub Actions) για να
ενημερώνει τα δεδομένα της ιστοσελίδας. Κάνει:
  1) Μαζεύει (resume-able, όπως τα desktop scripts) στατιστικά της
     τρέχουσας σεζόν για Champions League + Bundesliga
  2) Τραβάει βαθμολογίες Elo (clubelo.com, με fallback στο ήδη
     αποθηκευμένο elo_ratings.json αν αποτύχει)
  3) Φτιάχνει συνοπτικούς πίνακες ανά ομάδα (μέσος όρος shots/fouls/
     corners/cards κτλ, εντός/εκτός)
  4) Γράφει ΟΛΑ τα αποτελέσματα σε JSON μέσα στο docs/data/, που τα
     διαβάζει η ιστοσελίδα

Περιβαλλοντικές μεταβλητές που χρειάζεται (GitHub Secrets):
    API_FOOTBALL_KEY

Χρήση (τοπικά για δοκιμή):
    API_FOOTBALL_KEY=xxxx python update_all.py
"""

import os
import sys
import time
import json
from pathlib import Path
from datetime import datetime, timezone

import requests
import pandas as pd

BASE_URL = "https://v3.football.api-sports.io"
LEAGUES = {"bundesliga": 78}  # Champions League (id 2) θα προστεθεί ξανά αφού τελειοποιήσουμε τη δομή
SEASONS = [2022, 2023, 2024, 2025, 2026]  # όλες οι σεζόν που θέλουμε ιστορικό

CATEGORY_MAP = {
    "shots": "Total Shots",
    "shots_on_target": "Shots on Goal",
    "fouls": "Fouls",
    "corners": "Corner Kicks",
    "offside": "Offsides",
    "yellow_cards": "Yellow Cards",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
SITE_DATA_DIR = REPO_ROOT / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)


def api_get(api_key: str, endpoint: str, params: dict) -> dict:
    headers = {"x-apisports-key": api_key}
    resp = requests.get(f"{BASE_URL}/{endpoint}", headers=headers, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"API error: {data['errors']}")
    return data


def fetch_league_stats(api_key: str, league_id: int, season: int, csv_path: Path):
    """Ίδια resume-able λογική με τα desktop scripts: προσπερνάει αγώνες
    που έχουμε ήδη, μαζεύει μόνο τους νέους."""
    existing_df = pd.read_csv(csv_path, encoding="utf-8-sig") if csv_path.exists() else None
    done_ids = set(existing_df["fixture_id"].unique()) if existing_df is not None else set()

    data = api_get(api_key, "fixtures", {"league": league_id, "season": season})
    fixtures = data.get("response", [])
    finished = [f for f in fixtures if f["fixture"]["status"]["short"] == "FT"]
    todo = [f for f in finished if f["fixture"]["id"] not in done_ids]

    print(f"  League {league_id}: {len(finished)} ολοκληρωμένοι αγώνες, {len(todo)} νέοι.")

    new_rows = []
    for item in todo:
        fixture_id = item["fixture"]["id"]
        home_name = item["teams"]["home"]["name"]
        away_name = item["teams"]["away"]["name"]
        date = item["fixture"]["date"][:10]
        referee = item["fixture"].get("referee")

        try:
            stats_data = api_get(api_key, "fixtures/statistics", {"fixture": fixture_id})
        except Exception as e:
            print(f"    fixture {fixture_id} απέτυχε: {e}")
            time.sleep(0.3)
            continue
        time.sleep(0.3)

        stats_by_team = {}
        for team_block in stats_data.get("response", []):
            stats_by_team[team_block["team"]["name"]] = {s["type"]: s["value"] for s in team_block["statistics"]}

        for our_label, api_type in CATEGORY_MAP.items():
            home_val = stats_by_team.get(home_name, {}).get(api_type)
            away_val = stats_by_team.get(away_name, {}).get(api_type)
            new_rows.append({
                "fixture_id": fixture_id, "season": season, "date": date,
                "home_team": home_name, "away_team": away_name, "referee": referee,
                "category": our_label, "home_value": home_val, "away_value": away_val,
            })

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        combined = pd.concat([existing_df, new_df], ignore_index=True) if existing_df is not None else new_df
        combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"  Αποθηκεύτηκαν {len(new_rows) // len(CATEGORY_MAP)} νέοι αγώνες.")
        return combined
    return existing_df if existing_df is not None else pd.DataFrame()


def build_team_summary(df: pd.DataFrame) -> list:
    """Μέσος όρος ανά ομάδα (εντός+εκτός μαζί) - επιστρέφει λίστα από
    dicts, έτοιμη για JSON."""
    if df is None or df.empty:
        return []

    home_rows = df[["category", "home_team", "home_value"]].rename(columns={"home_team": "team", "home_value": "value"})
    away_rows = df[["category", "away_team", "away_value"]].rename(columns={"away_team": "team", "away_value": "value"})
    all_rows = pd.concat([home_rows, away_rows], ignore_index=True)
    all_rows["value"] = pd.to_numeric(all_rows["value"], errors="coerce")

    grouped = all_rows.groupby(["team", "category"])["value"].mean().reset_index()
    counts = all_rows.groupby(["team", "category"])["value"].count().reset_index()
    max_counts = counts.groupby("team")["value"].max().reset_index().rename(columns={"value": "matches"})

    wide = grouped.pivot_table(index="team", columns="category", values="value").reset_index()
    wide = wide.merge(max_counts, on="team", how="left")
    wide = wide.round(1)

    return wide.to_dict(orient="records")


def fetch_elo_ratings() -> dict:
    """Δοκιμάζει clubelo.com· αν αποτύχει, κρατάει το ήδη υπάρχον αρχείο."""
    fallback_path = SITE_DATA_DIR / "elo_ratings.json"
    try:
        from datetime import date
        today = date.today().isoformat()
        resp = requests.get(
            f"http://api.clubelo.com/{today}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        resp.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))
        ratings = dict(zip(df["Club"], df["Elo"]))
        print(f"  Elo: πήρα {len(ratings)} ομάδες από clubelo.com")
        return ratings
    except Exception as e:
        print(f"  Elo: απέτυχε το clubelo.com ({e}), κρατάω το προηγούμενο αρχείο")
        if fallback_path.exists():
            with open(fallback_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}


def main():
    api_key = os.environ.get("API_FOOTBALL_KEY")
    if not api_key:
        print("ΛΕΙΠΕΙ το API_FOOTBALL_KEY (environment variable). Σταματώ.")
        sys.exit(1)

    summary = {}
    for league_name, league_id in LEAGUES.items():
        print(f"\n=== {league_name} ===")
        csv_path = DATA_DIR / f"{league_name}_stats.csv"

        df = None
        for season in SEASONS:
            print(f" Σεζόν {season}:")
            df = fetch_league_stats(api_key, league_id, season, csv_path)
            time.sleep(1)  # μικρή ανάσα ανάμεσα σε σεζόν

        team_summary = build_team_summary(df)
        summary[league_name] = team_summary

        with open(SITE_DATA_DIR / f"{league_name}_teams.json", "w", encoding="utf-8") as f:
            json.dump(team_summary, f, ensure_ascii=False, indent=2)
        print(f"  Γράφτηκε: docs/data/{league_name}_teams.json ({len(team_summary)} ομάδες)")

    print("\n=== Elo ===")
    elo_ratings = fetch_elo_ratings()
    with open(SITE_DATA_DIR / "elo_ratings.json", "w", encoding="utf-8") as f:
        json.dump(elo_ratings, f, ensure_ascii=False, indent=2)
    print(f"  Γράφτηκε: docs/data/elo_ratings.json ({len(elo_ratings)} ομάδες)")

    with open(SITE_DATA_DIR / "last_updated.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": datetime.now(timezone.utc).isoformat()}, f)

    print("\nΤέλος.")


if __name__ == "__main__":
    main()
