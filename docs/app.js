const CATEGORY_COLUMNS = ["matches", "shots", "shots_on_target", "fouls", "corners", "offside", "yellow_cards"];

async function loadJson(path) {
  const resp = await fetch(path);
  if (!resp.ok) return null;
  return resp.json();
}

function renderTeamTable(teams, bodyId) {
  const body = document.getElementById(bodyId);
  body.innerHTML = "";
  if (!teams || teams.length === 0) {
    body.innerHTML = `<tr><td colspan="8">Δεν υπάρχουν δεδομένα ακόμα.</td></tr>`;
    return;
  }
  teams.sort((a, b) => a.team.localeCompare(b.team));
  for (const row of teams) {
    const tr = document.createElement("tr");
    let html = `<td>${row.team}</td>`;
    for (const col of CATEGORY_COLUMNS) {
      const val = row[col];
      html += `<td>${val !== undefined && val !== null ? val : "-"}</td>`;
    }
    tr.innerHTML = html;
    body.appendChild(tr);
  }
}

function renderEloTable(eloRatings) {
  const body = document.getElementById("elo-table-body");
  body.innerHTML = "";
  if (!eloRatings) return;

  const entries = Object.entries(eloRatings).sort((a, b) => b[1] - a[1]);
  entries.forEach(([team, elo], idx) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${idx + 1}</td><td>${team}</td><td>${Math.round(elo)}</td>`;
    body.appendChild(tr);
  });
}

let currentLeague = "bundesliga";

async function showLeague(league) {
  currentLeague = league;
  const teams = await loadJson(`data/${league}_teams.json`);
  renderTeamTable(teams, "team-table-body");
  document.getElementById("team-view").style.display = "block";
  document.getElementById("elo-view").style.display = "none";
  document.getElementById("form-view").style.display = "none";
}

async function showForm() {
  const form = await loadJson(`data/${currentLeague}_form.json`);
  renderTeamTable(form, "form-table-body");
  document.getElementById("team-view").style.display = "none";
  document.getElementById("elo-view").style.display = "none";
  document.getElementById("form-view").style.display = "block";
}

async function showElo() {
  const elo = await loadJson("data/elo_ratings.json");
  renderEloTable(elo);
  document.getElementById("team-view").style.display = "none";
  document.getElementById("form-view").style.display = "none";
  document.getElementById("elo-view").style.display = "block";
}

async function showLastUpdated() {
  const info = await loadJson("data/last_updated.json");
  const el = document.getElementById("last-updated");
  if (info && info.updated_at) {
    const d = new Date(info.updated_at);
    el.textContent = "Τελευταία ενημέρωση: " + d.toLocaleString("el-GR");
  } else {
    el.textContent = "Δεν έχει τρέξει ακόμα ενημέρωση.";
  }
}

document.querySelectorAll(".tab-button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");

    if (btn.dataset.view === "elo") {
      showElo();
    } else if (btn.dataset.view === "form") {
      showForm();
    } else if (btn.dataset.league) {
      showLeague(btn.dataset.league);
    }
  });
});

showLeague("bundesliga");
showLastUpdated();
