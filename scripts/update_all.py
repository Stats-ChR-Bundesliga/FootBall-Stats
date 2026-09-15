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

# Το API-Football μερικές φορές επιστρέφει διαφορετική γραφή του ίδιου
# ονόματος ανάλογα με τη σεζόν (π.χ. "Bayern Munich" αντί "Bayern München").
TEAM_NAME_ALIASES = {
    "Bayern Munich": "Bayern München",
    "Borussia Monchengladbach": "Borussia Mönchengladbach",
    "FC Heidenheim": "1. FC Heidenheim",
    "Vfl Bochum": "VfL Bochum",
}


def canonical_team_name(name: str) -> str:
    import unicodedata
    if not name:
        return name
    name = unicodedata.normalize("NFC", name)
    return TEAM_NAME_ALIASES.get(name, name)

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


def fetch_match_card_events(api_key: str, fixture_id: int, home_team_id: int, away_team_id: int, window_minutes: int = 2):
    """
    Τραβάει τα events (κάρτες) του αγώνα και υπολογίζει "προσαρμοσμένο"
    σύνολο καρτών ανά ομάδα: κάρτες που δόθηκαν εντός `window_minutes`
    λεπτών η μία από την άλλη (και των δύο ομάδων μαζί - συνήθως
    τσακωμός) μετράνε 0.5 η καθεμία αντί για 1.
    Επιστρέφει (home_adjusted, away_adjusted) ή (None, None) αν αποτύχει.
    """
    try:
        data = api_get(api_key, "fixtures/events", {"fixture": fixture_id})
    except Exception:
        return None, None

    events = data.get("response", [])
    yellow_events = [
        e for e in events
        if e.get("type") == "Card" and e.get("detail") in ("Yellow Card", "Second Yellow card")
    ]
    if not yellow_events:
        return 0.0, 0.0

    # Ταξινόμηση κατά λεπτό, μετά ομαδοποίηση σε "συστάδες" (clusters) όπου
    # κάθε επόμενη κάρτα απέχει το πολύ `window_minutes` από την προηγούμενη
    # της ίδιας συστάδας (αλυσιδωτά - like a sliding cluster).
    yellow_events.sort(key=lambda e: e.get("time", {}).get("elapsed") or 0)

    clusters = []
    current_cluster = []
    last_minute = None
    for e in yellow_events:
        minute = e.get("time", {}).get("elapsed") or 0
        if last_minute is not None and (minute - last_minute) > window_minutes:
            clusters.append(current_cluster)
            current_cluster = []
        current_cluster.append(e)
        last_minute = minute
    if current_cluster:
        clusters.append(current_cluster)

    home_total, away_total = 0.0, 0.0
    for cluster in clusters:
        weight = 0.5 if len(cluster) >= 2 else 1.0
        for e in cluster:
            team_id = e.get("team", {}).get("id")
            if team_id == home_team_id:
                home_total += weight
            elif team_id == away_team_id:
                away_total += weight

    return round(home_total, 1), round(away_total, 1)


def fetch_league_stats(api_key: str, league_id: int, season: int, csv_path: Path):
    """Ίδια resume-able λογική με τα desktop scripts: προσπερνάει αγώνες
    που έχουμε ήδη, μαζεύει μόνο τους νέους."""
    existing_df = pd.read_csv(csv_path, encoding="utf-8-sig") if csv_path.exists() else None
    if existing_df is not None:
        # Καθαρίζουμε τυχόν παλιά "ακατέργαστα" ονόματα σε κάθε τρέξιμο
        existing_df["home_team"] = existing_df["home_team"].apply(canonical_team_name)
        existing_df["away_team"] = existing_df["away_team"].apply(canonical_team_name)
    done_ids = set(existing_df["fixture_id"].unique()) if existing_df is not None else set()

    data = api_get(api_key, "fixtures", {"league": league_id, "season": season})
    fixtures = data.get("response", [])
    finished = [f for f in fixtures if f["fixture"]["status"]["short"] == "FT"]
    todo = [f for f in finished if f["fixture"]["id"] not in done_ids]

    print(f"  League {league_id}: {len(finished)} ολοκληρωμένοι αγώνες, {len(todo)} νέοι.")

    new_rows = []
    for item in todo:
        fixture_id = item["fixture"]["id"]
        home_id = item["teams"]["home"]["id"]
        away_id = item["teams"]["away"]["id"]
        home_name = canonical_team_name(item["teams"]["home"]["name"])
        away_name = canonical_team_name(item["teams"]["away"]["name"])
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

        # Προσαρμοσμένες κάρτες (μισό βάρος σε "τσακωμούς" ίδιου λεπτού)
        adjusted_home_cards, adjusted_away_cards = fetch_match_card_events(api_key, fixture_id, home_id, away_id)
        time.sleep(0.3)

        for our_label, api_type in CATEGORY_MAP.items():
            if our_label == "yellow_cards" and adjusted_home_cards is not None:
                home_val, away_val = adjusted_home_cards, adjusted_away_cards
            else:
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
        print(f"  Αποθηκεύτηκαν {len(new_rows) // len(CATEGORY_MAP)} νέοι αγώνες.")
    else:
        combined = existing_df if existing_df is not None else pd.DataFrame()

    if not combined.empty:
        combined.to_csv(csv_path, index=False, encoding="utf-8-sig")  # πάντα αποθηκεύουμε (και τον καθαρισμό ονομάτων)
    return combined


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


def trimmed_mean(values: list) -> float:
    """Μέσος όρος χωρίς το μεγαλύτερο και το μικρότερο - αποφεύγει να
    στρεβλώνει ένα ακραίο μεμονωμένο ματς τη φόρμα. Χρειάζεται
    τουλάχιστον 4 τιμές (αλλιώς απλός μέσος όρος)."""
    if len(values) < 4:
        return sum(values) / len(values)
    sorted_vals = sorted(values)
    trimmed = sorted_vals[1:-1]
    return sum(trimmed) / len(trimmed)


def build_team_form(df: pd.DataFrame, n_matches: int = 10) -> list:
    """Δυναμική φόρμα: κομμένος μέσος όρος κάθε ομάδας στους τελευταίους
    N αγώνες της (εντός+εκτός μαζί), ταξινομημένους χρονολογικά."""
    if df is None or df.empty:
        return []

    home_rows = df[["date", "category", "home_team", "home_value"]].rename(columns={"home_team": "team", "home_value": "value"})
    away_rows = df[["date", "category", "away_team", "away_value"]].rename(columns={"away_team": "team", "away_value": "value"})
    all_rows = pd.concat([home_rows, away_rows], ignore_index=True)
    all_rows["value"] = pd.to_numeric(all_rows["value"], errors="coerce")
    all_rows["date"] = pd.to_datetime(all_rows["date"])

    results = []
    for (team, category), group in all_rows.groupby(["team", "category"]):
        recent = group.dropna(subset=["value"]).sort_values("date", ascending=False).head(n_matches)
        if recent.empty:
            continue
        results.append({
            "team": team, "category": category,
            "form_avg": trimmed_mean(recent["value"].tolist()),
            "matches_used": len(recent),
        })

    if not results:
        return []

    long_df = pd.DataFrame(results)
    wide = long_df.pivot_table(index="team", columns="category", values="form_avg", aggfunc="first").reset_index()
    meta = long_df.groupby("team")["matches_used"].max().reset_index().rename(columns={"matches_used": "matches"})
    wide = wide.merge(meta, on="team", how="left")
    wide = wide.round(1)
    return wide.to_dict(orient="records")


def build_referee_profiles(df: pd.DataFrame) -> list:
    """Μέσος όρος ΣΥΝΟΛΟΥ ΑΓΩΝΑ (όχι ανά ομάδα) φάουλ/καρτών ανά διαιτητή -
    το νούμερο που συγκρίνεται με τις γραμμές Over/Under bookmaker."""
    if df is None or df.empty or "referee" not in df.columns:
        return []

    relevant = df[df["category"].isin(["fouls", "yellow_cards"])].copy()
    relevant["home_value"] = pd.to_numeric(relevant["home_value"], errors="coerce")
    relevant["away_value"] = pd.to_numeric(relevant["away_value"], errors="coerce")
    relevant["match_total"] = relevant["home_value"] + relevant["away_value"]
    relevant = relevant.dropna(subset=["referee", "match_total"])
    relevant = relevant[relevant["referee"].astype(str).str.strip() != ""]

    if relevant.empty:
        return []

    summary = relevant.groupby(["referee", "category"])["match_total"].agg(average="mean", matches="count").reset_index()
    wide = summary.pivot_table(index="referee", columns="category", values="average", aggfunc="first").reset_index()
    matches_col = summary.groupby("referee")["matches"].max().reset_index()
    wide = wide.merge(matches_col, on="referee", how="left")
    wide = wide.round(1)
    return wide.rename(columns={"referee": "referee_name"}).to_dict(orient="records")


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


# ================== ΕΚΤΙΜΗΣΗ ΕΠΟΜΕΝΗΣ ΑΓΩΝΙΣΤΙΚΗΣ ==================

import unicodedata
import difflib

ELO_SCALE_DIVISOR = 400.0
ELO_BASE = 1500.0
ELO_HOME_ADVANTAGE = 80.0
ELO_SPLIT_WEIGHT = 0.35
ELO_FOUL_BUMP_MAX = 3.0
ELO_CARD_BUMP_MAX = 1.0
SPLIT_ADJUST_CATEGORIES = {"shots", "shots_on_target", "corners"}
UNDERDOG_BUMP_CATEGORIES = {"fouls", "yellow_cards"}
CATEGORIES_WITH_DEFENSE = {"shots", "shots_on_target", "fouls", "corners", "yellow_cards"}
WEIGHTS = {"form": 0.4, "own_venue": 0.25, "opponent_other_venue": 0.15, "referee": 0.2}
REFEREE_WEIGHT_OVERRIDES = {"fouls": 0.35, "yellow_cards": 0.35}  # μόνο για αυτά τα δύο


def normalize_name(name: str) -> str:
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    return "".join(n.lower().split())


def find_elo_fuzzy(elo_dict: dict, team_name: str):
    if team_name in elo_dict:
        return elo_dict[team_name]
    norm_target = normalize_name(team_name)
    best_match, best_score = None, 0.0
    for candidate, elo in elo_dict.items():
        norm_candidate = normalize_name(candidate)
        if norm_candidate == norm_target or norm_candidate in norm_target or norm_target in norm_candidate:
            return elo
        score = difflib.SequenceMatcher(None, norm_target, norm_candidate).ratio()
        if score > best_score:
            best_match, best_score = elo, score
    return best_match if best_score >= 0.75 else None


def elo_expected_score(elo_home, elo_away):
    return 1.0 / (1.0 + 10 ** ((elo_away - (elo_home + ELO_HOME_ADVANTAGE)) / ELO_SCALE_DIVISOR))


def elo_match_probabilities(elo_home, elo_away):
    expected = elo_expected_score(elo_home, elo_away)
    draw_max, draw_min = 0.28, 0.05
    imbalance = abs(expected - 0.5) / 0.5
    draw_prob = max(draw_min, min(draw_max, draw_max - (draw_max - draw_min) * imbalance))
    return expected - draw_prob / 2, draw_prob, (1 - expected) - draw_prob / 2


def build_historical_long(df: pd.DataFrame):
    """Ίδια λογική με τα desktop scripts: προσθέτει και τις 'αμυντικές'
    (_against) εκδοχές κάθε κατηγορίας."""
    if df is None or df.empty:
        return None
    ref_col = df["referee"] if "referee" in df.columns else None
    home_rows = df[["fixture_id", "category", "home_team", "home_value"]].rename(columns={"home_team": "team", "home_value": "value"})
    home_rows["venue"] = "home"
    away_rows = df[["fixture_id", "category", "away_team", "away_value"]].rename(columns={"away_team": "team", "away_value": "value"})
    away_rows["venue"] = "away"
    if ref_col is not None:
        home_rows["referee"] = ref_col
        away_rows["referee"] = ref_col
    all_rows = pd.concat([home_rows, away_rows], ignore_index=True)
    all_rows["value"] = pd.to_numeric(all_rows["value"], errors="coerce")

    defense_frames = []
    for category in CATEGORIES_WITH_DEFENSE:
        cat_rows = all_rows[all_rows["category"] == category].copy()
        if cat_rows.empty:
            continue
        opp_lookup = cat_rows.set_index(["fixture_id", "venue"])["value"]
        cat_rows["opp_venue"] = cat_rows["venue"].map({"home": "away", "away": "home"})
        cat_rows["value"] = cat_rows.apply(lambda r: opp_lookup.get((r["fixture_id"], r["opp_venue"])), axis=1)
        cat_rows["category"] = f"{category}_against"
        defense_frames.append(cat_rows.drop(columns=["opp_venue"]))
    if defense_frames:
        all_rows = pd.concat([all_rows] + defense_frames, ignore_index=True)
    return all_rows


def historical_venue_avg(all_rows, team, category, venue):
    if all_rows is None:
        return None
    rows = all_rows[(all_rows["team"] == team) & (all_rows["category"] == category) & (all_rows["venue"] == venue)]
    rows = rows.dropna(subset=["value"])
    return rows["value"].mean() if not rows.empty else None


def referee_avg(all_rows, referee_name, category):
    if all_rows is None or not referee_name or "referee" not in all_rows.columns:
        return None
    rows = all_rows[(all_rows["referee"] == referee_name) & (all_rows["category"] == category)]
    rows = rows.dropna(subset=["value"])
    return rows["value"].mean() if not rows.empty else None


def get_team_match_count(all_rows, team):
    if all_rows is None:
        return 0
    return all_rows[all_rows["team"] == team]["fixture_id"].nunique()


def compute_elo_trust(home_count, away_count, min_matches=5):
    weakest = min(home_count, away_count)
    return max(0.2, min(1.0, weakest / min_matches))


def get_team_recent_form(api_key, team_id, last_n=10):
    fixtures_data = api_get(api_key, "fixtures", {"team": team_id, "last": last_n})
    fixtures = fixtures_data.get("response", [])
    values_by_category = {cat: [] for cat in CATEGORY_MAP}
    for item in fixtures:
        fixture_id = item["fixture"]["id"]
        try:
            stats_data = api_get(api_key, "fixtures/statistics", {"fixture": fixture_id})
        except Exception:
            time.sleep(0.3)
            continue
        time.sleep(0.3)
        stats_by_team = {}
        for team_block in stats_data.get("response", []):
            stats_by_team[team_block["team"]["id"]] = {s["type"]: s["value"] for s in team_block["statistics"]}
        team_stats = stats_by_team.get(team_id, {})
        for our_label, api_type in CATEGORY_MAP.items():
            val = team_stats.get(api_type)
            if val is not None:
                values_by_category[our_label].append(val)
    return {cat: trimmed_mean(vals) for cat, vals in values_by_category.items() if vals}


def combine_estimate(form_avg, own_venue_avg, opp_venue_avg, referee_val=None, referee_weight=None):
    parts, weights = [], []
    if form_avg is not None:
        parts.append(form_avg); weights.append(WEIGHTS["form"])
    if own_venue_avg is not None:
        parts.append(own_venue_avg); weights.append(WEIGHTS["own_venue"])
    if opp_venue_avg is not None:
        parts.append(opp_venue_avg); weights.append(WEIGHTS["opponent_other_venue"])
    if referee_val is not None:
        parts.append(referee_val)
        weights.append(referee_weight if referee_weight is not None else WEIGHTS["referee"])
    if not parts:
        return None
    total_w = sum(weights)
    return sum(p * w for p, w in zip(parts, weights)) / total_w


def apply_elo_adjustment(category, home_est, away_est, expected_score, elo_trust=1.0):
    if home_est is None or away_est is None:
        return home_est, away_est
    if category in SPLIT_ADJUST_CATEGORIES:
        total = home_est + away_est
        original_home_share = home_est / total if total else 0.5
        effective_weight = ELO_SPLIT_WEIGHT * elo_trust
        blended_share = (1 - effective_weight) * original_home_share + effective_weight * expected_score
        new_home = total * blended_share
        return round(new_home, 1), round(total - new_home, 1)
    if category in UNDERDOG_BUMP_CATEGORIES:
        gap = abs(expected_score - 0.5) * 2
        max_bump = ELO_CARD_BUMP_MAX if category == "yellow_cards" else ELO_FOUL_BUMP_MAX
        bump = gap * max_bump * elo_trust
        if expected_score > 0.5:
            return round(home_est, 1), round(away_est + bump, 1)
        return round(home_est + bump, 1), round(away_est, 1)
    return home_est, away_est


def get_next_matchday_fixtures(api_key, league_id, season):
    """Βρίσκει αυτόματα την επόμενη αγωνιστική - παίρνει τους αγώνες
    που δεν έχουν παιχτεί ακόμα και τους ομαδοποιεί στο πρώτο 4ήμερο."""
    data = api_get(api_key, "fixtures", {"league": league_id, "season": season})
    fixtures = data.get("response", [])
    upcoming = [f for f in fixtures if f["fixture"]["status"]["short"] in ("NS", "TBD")]
    if not upcoming:
        return []
    upcoming.sort(key=lambda f: f["fixture"]["date"])
    from datetime import datetime, timedelta
    first_date = datetime.fromisoformat(upcoming[0]["fixture"]["date"].replace("Z", "+00:00"))
    window_end = first_date + timedelta(days=4)
    return [
        f for f in upcoming
        if datetime.fromisoformat(f["fixture"]["date"].replace("Z", "+00:00")) <= window_end
    ]


def load_derbies():
    """Διαβάζει το derbies.json (αν υπάρχει) - λίστα γνωστών ντέρμπι/
    αντιπαλοτήτων. Επεξεργάσιμο χειροκίνητα από τον χρήστη."""
    derbies_path = REPO_ROOT / "derbies.json"
    if not derbies_path.exists():
        return []
    with open(derbies_path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_derby_match(derbies, home_name, away_name):
    home_norm, away_norm = normalize_name(home_name), normalize_name(away_name)
    for d in derbies:
        t1, t2 = normalize_name(d["team1"]), normalize_name(d["team2"])
        if {home_norm, away_norm} == {t1, t2}:
            return d.get("name", "Ντέρμπι")
    return None


def build_matchday_estimates(api_key, league_id, season, historical_df, elo_ratings, referee_overrides=None):
    fixtures = get_next_matchday_fixtures(api_key, league_id, season)
    if not fixtures:
        print("  Δεν βρέθηκαν επόμενοι αγώνες.")
        return []

    historical_rows = build_historical_long(historical_df)
    derbies = load_derbies()
    form_cache = {}
    results = []

    for item in fixtures:
        home_id, away_id = item["teams"]["home"]["id"], item["teams"]["away"]["id"]
        home_name = canonical_team_name(item["teams"]["home"]["name"])
        away_name = canonical_team_name(item["teams"]["away"]["name"])
        date = item["fixture"]["date"][:10]
        referee = item["fixture"].get("referee")

        home_elo = find_elo_fuzzy(elo_ratings, home_name) or ELO_BASE
        away_elo = find_elo_fuzzy(elo_ratings, away_name) or ELO_BASE
        expected_score = elo_expected_score(home_elo, away_elo)
        p_home, p_draw, p_away = elo_match_probabilities(home_elo, away_elo)

        home_cnt = get_team_match_count(historical_rows, home_name)
        away_cnt = get_team_match_count(historical_rows, away_name)
        elo_trust = compute_elo_trust(home_cnt, away_cnt)

        if home_id not in form_cache:
            print(f"    Φόρμα: {home_name}...")
            form_cache[home_id] = get_team_recent_form(api_key, home_id)
        if away_id not in form_cache:
            print(f"    Φόρμα: {away_name}...")
            form_cache[away_id] = get_team_recent_form(api_key, away_id)

        derby_name = is_derby_match(derbies, home_name, away_name)

        row = {
            "home_team": home_name, "away_team": away_name, "date": date, "referee": referee,
            "home_elo": round(home_elo, 1), "away_elo": round(away_elo, 1),
            "p_home_win": round(p_home, 3), "p_draw": round(p_draw, 3), "p_away_win": round(p_away, 3),
            "expected_score": round(expected_score, 4), "elo_trust": round(elo_trust, 3),
            "is_derby": derby_name is not None, "derby_name": derby_name,
            "components": {},
        }
        for category in CATEGORY_MAP:
            home_form = form_cache[home_id].get(category)
            away_form = form_cache[away_id].get(category)
            home_own = historical_venue_avg(historical_rows, home_name, category, "home")
            away_own = historical_venue_avg(historical_rows, away_name, category, "away")
            if category in CATEGORIES_WITH_DEFENSE:
                home_opp = historical_venue_avg(historical_rows, away_name, f"{category}_against", "away")
                away_opp = historical_venue_avg(historical_rows, home_name, f"{category}_against", "home")
            else:
                home_opp = historical_venue_avg(historical_rows, away_name, category, "away")
                away_opp = historical_venue_avg(historical_rows, home_name, category, "home")
            ref_val = referee_avg(historical_rows, referee, category)

            row["components"][category] = {
                "home_form": home_form, "away_form": away_form,
                "home_own": home_own, "away_own": away_own,
                "home_opp": home_opp, "away_opp": away_opp,
                "referee": ref_val,
                "is_referee_boosted": category in REFEREE_WEIGHT_OVERRIDES,
            }

            ref_weight = REFEREE_WEIGHT_OVERRIDES.get(category)
            home_est = combine_estimate(home_form, home_own, home_opp, referee_val=ref_val, referee_weight=ref_weight)
            away_est = combine_estimate(away_form, away_own, away_opp, referee_val=ref_val, referee_weight=ref_weight)
            home_est, away_est = apply_elo_adjustment(category, home_est, away_est, expected_score, elo_trust=elo_trust)

            row[f"home_{category}"] = home_est
            row[f"away_{category}"] = away_est
            if home_est is not None and away_est is not None:
                row[f"total_{category}"] = round(home_est + away_est, 1)

        results.append(row)

    return results


def main():
    api_key = os.environ.get("API_FOOTBALL_KEY")
    if not api_key:
        print("ΛΕΙΠΕΙ το API_FOOTBALL_KEY (environment variable). Σταματώ.")
        sys.exit(1)

    # Εξάγουμε τα προεπιλεγμένα βάρη, ώστε η ιστοσελίδα να ξέρει πού να
    # βάλει αρχικά τα sliders (ο χρήστης μπορεί μετά να τα αλλάξει ζωντανά).
    model_config = {
        "weights": WEIGHTS,
        "referee_weight_overrides": REFEREE_WEIGHT_OVERRIDES,
        "elo_split_weight": ELO_SPLIT_WEIGHT,
        "elo_foul_bump_max": ELO_FOUL_BUMP_MAX,
        "elo_card_bump_max": ELO_CARD_BUMP_MAX,
        "derby_boost_pct": 0.20,
    }
    with open(SITE_DATA_DIR / "model_config.json", "w", encoding="utf-8") as f:
        json.dump(model_config, f, ensure_ascii=False, indent=2)

    print("\n=== Elo ===")
    elo_ratings = fetch_elo_ratings()
    with open(SITE_DATA_DIR / "elo_ratings.json", "w", encoding="utf-8") as f:
        json.dump(elo_ratings, f, ensure_ascii=False, indent=2)
    print(f"  Γράφτηκε: docs/data/elo_ratings.json ({len(elo_ratings)} ομάδες)")

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
        team_form = build_team_form(df, n_matches=10)
        summary[league_name] = team_summary

        with open(SITE_DATA_DIR / f"{league_name}_teams.json", "w", encoding="utf-8") as f:
            json.dump(team_summary, f, ensure_ascii=False, indent=2)
        print(f"  Γράφτηκε: docs/data/{league_name}_teams.json ({len(team_summary)} ομάδες)")

        with open(SITE_DATA_DIR / f"{league_name}_form.json", "w", encoding="utf-8") as f:
            json.dump(team_form, f, ensure_ascii=False, indent=2)
        print(f"  Γράφτηκε: docs/data/{league_name}_form.json ({len(team_form)} ομάδες)")

        referee_profiles = build_referee_profiles(df)
        with open(SITE_DATA_DIR / f"{league_name}_referees.json", "w", encoding="utf-8") as f:
            json.dump(referee_profiles, f, ensure_ascii=False, indent=2)
        print(f"  Γράφτηκε: docs/data/{league_name}_referees.json ({len(referee_profiles)} διαιτητές)")

        print(f"\n  --- Εκτίμηση επόμενης αγωνιστικής ({league_name}) ---")
        try:
            matchday = build_matchday_estimates(api_key, league_id, SEASONS[-1], df, elo_ratings)
        except Exception as e:
            print(f"  Απέτυχε η εκτίμηση αγωνιστικής: {e}")
            matchday = []
        with open(SITE_DATA_DIR / f"{league_name}_upcoming.json", "w", encoding="utf-8") as f:
            json.dump(matchday, f, ensure_ascii=False, indent=2)
        print(f"  Γράφτηκε: docs/data/{league_name}_upcoming.json ({len(matchday)} αγώνες)")

    with open(SITE_DATA_DIR / "last_updated.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": datetime.now(timezone.utc).isoformat()}, f)

    print("\nΤέλος.")


if __name__ == "__main__":
    main()
