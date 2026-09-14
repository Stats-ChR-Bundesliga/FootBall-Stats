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

function renderRefereeTable(referees) {
  const body = document.getElementById("referees-table-body");
  body.innerHTML = "";
  if (!referees || referees.length === 0) {
    body.innerHTML = `<tr><td colspan="4">Δεν υπάρχουν δεδομένα ακόμα.</td></tr>`;
    return;
  }
  referees.sort((a, b) => (b.matches || 0) - (a.matches || 0));
  for (const row of referees) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${row.referee_name}</td><td>${row.matches ?? "-"}</td><td>${row.fouls ?? "-"}</td><td>${row.yellow_cards ?? "-"}</td>`;
    body.appendChild(tr);
  }
}

function renderUpcoming(matches) {
  const container = document.getElementById("upcoming-view");
  container.innerHTML = "";
  if (!matches || matches.length === 0) {
    container.innerHTML = `<p>Δεν υπάρχουν επόμενοι αγώνες αυτή τη στιγμή.</p>`;
    return;
  }
  const catLabels = {
    shots: "Σουτ", shots_on_target: "Σουτ στο στόχο", fouls: "Φάουλ",
    corners: "Κόρνερ", offside: "Οφσάιντ", yellow_cards: "Κίτρινες",
  };
  for (const m of matches) {
    const card = document.createElement("div");
    card.className = "match-card";
    let rowsHtml = "";
    for (const [key, label] of Object.entries(catLabels)) {
      const total = m[`total_${key}`];
      if (total !== undefined && total !== null) {
        rowsHtml += `<tr><td>${label}</td><td>${m[`home_${key}`] ?? "-"}</td><td>${m[`away_${key}`] ?? "-"}</td><td><strong>${total}</strong></td></tr>`;
      }
    }
    card.innerHTML = `
      <h3>${m.home_team} vs ${m.away_team}</h3>
      <p class="match-meta">${m.date || ""} ${m.referee ? "· Διαιτητής: " + m.referee : ""}</p>
      <p class="match-meta">Elo: ${m.home_elo} / ${m.away_elo} · 1: ${(m.p_home_win*100).toFixed(0)}% Χ: ${(m.p_draw*100).toFixed(0)}% 2: ${(m.p_away_win*100).toFixed(0)}%</p>
      <table class="mini-table">
        <thead><tr><th>Κατηγορία</th><th>${m.home_team}</th><th>${m.away_team}</th><th>Σύνολο</th></tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    `;
    container.appendChild(card);
  }
}

async function showUpcoming() {
  const matches = await loadJson(`data/${currentLeague}_upcoming.json`);
  renderUpcoming(matches);
  document.getElementById("team-view").style.display = "none";
  document.getElementById("elo-view").style.display = "none";
  document.getElementById("form-view").style.display = "none";
  document.getElementById("referees-view").style.display = "none";
  document.getElementById("upcoming-view").style.display = "block";
}

let currentLeague = "bundesliga";

async function showLeague(league) {
  currentLeague = league;
  const teams = await loadJson(`data/${league}_teams.json`);
  renderTeamTable(teams, "team-table-body");
  document.getElementById("team-view").style.display = "block";
  document.getElementById("elo-view").style.display = "none";
  document.getElementById("form-view").style.display = "none";
  document.getElementById("referees-view").style.display = "none";
  document.getElementById("upcoming-view").style.display = "none";
}

async function showForm() {
  const form = await loadJson(`data/${currentLeague}_form.json`);
  renderTeamTable(form, "form-table-body");
  document.getElementById("team-view").style.display = "none";
  document.getElementById("elo-view").style.display = "none";
  document.getElementById("form-view").style.display = "block";
  document.getElementById("referees-view").style.display = "none";
  document.getElementById("upcoming-view").style.display = "none";
}

async function showReferees() {
  const referees = await loadJson(`data/${currentLeague}_referees.json`);
  renderRefereeTable(referees);
  document.getElementById("team-view").style.display = "none";
  document.getElementById("elo-view").style.display = "none";
  document.getElementById("form-view").style.display = "none";
  document.getElementById("referees-view").style.display = "block";
  document.getElementById("upcoming-view").style.display = "none";
}

async function showElo() {
  const elo = await loadJson("data/elo_ratings.json");
  renderEloTable(elo);
  document.getElementById("team-view").style.display = "none";
  document.getElementById("form-view").style.display = "none";
  document.getElementById("referees-view").style.display = "none";
  document.getElementById("upcoming-view").style.display = "none";
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
    } else if (btn.dataset.view === "referees") {
      showReferees();
    } else if (btn.dataset.view === "upcoming") {
      showUpcoming();
    } else if (btn.dataset.league) {
      showLeague(btn.dataset.league);
    }
  });
});

showLeague("bundesliga");
showLastUpdated();
