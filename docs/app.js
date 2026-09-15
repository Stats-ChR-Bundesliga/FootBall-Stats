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
  const container = document.getElementById("upcoming-matches");
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
      <h3>${m.home_team} vs ${m.away_team} ${m.is_derby ? '<span class="derby-badge">🔥 ' + (m.derby_name || 'Ντέρμπι') + '</span>' : ''}</h3>
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
  lastLoadedMatches = matches;
  if (!modelConfig) {
    modelConfig = await loadJson("data/model_config.json");
    initSliders();
  }
  if (matches && matches.length > 0) {
    refreshUpcomingWithWeights();
  } else {
    renderUpcoming(matches);
  }
  document.getElementById("team-view").style.display = "none";
  document.getElementById("elo-view").style.display = "none";
  document.getElementById("form-view").style.display = "none";
  document.getElementById("referees-view").style.display = "none";
  document.getElementById("upcoming-view").style.display = "block";
}

// ================== ΔΙΑΔΡΑΣΤΙΚΟ ΜΟΝΤΕΛΟ (sliders) ==================

let modelConfig = null;
let userWeights = null; // αντικαθίσταται με τις τρέχουσες τιμές sliders

function combineEstimateJS(formAvg, ownAvg, oppAvg, refAvg, weights, refWeight) {
  const parts = [];
  const w = [];
  if (formAvg !== null && formAvg !== undefined) { parts.push(formAvg); w.push(weights.form); }
  if (ownAvg !== null && ownAvg !== undefined) { parts.push(ownAvg); w.push(weights.own_venue); }
  if (oppAvg !== null && oppAvg !== undefined) { parts.push(oppAvg); w.push(weights.opponent_other_venue); }
  if (refAvg !== null && refAvg !== undefined) {
    parts.push(refAvg);
    w.push(refWeight !== undefined && refWeight !== null ? refWeight : weights.referee);
  }
  if (parts.length === 0) return null;
  const totalW = w.reduce((a, b) => a + b, 0);
  let sum = 0;
  for (let i = 0; i < parts.length; i++) sum += parts[i] * w[i];
  return sum / totalW;
}

function applyEloAdjustmentJS(category, homeEst, awayEst, expectedScore, eloTrust, eloSplitWeight, foulBumpMax, cardBumpMax) {
  if (homeEst === null || awayEst === null) return [homeEst, awayEst];
  const SPLIT_CATS = ["shots", "shots_on_target", "corners"];
  const BUMP_CATS = ["fouls", "yellow_cards"];

  if (SPLIT_CATS.includes(category)) {
    const total = homeEst + awayEst;
    const originalShare = total ? homeEst / total : 0.5;
    const effectiveWeight = eloSplitWeight * eloTrust;
    const blendedShare = (1 - effectiveWeight) * originalShare + effectiveWeight * expectedScore;
    const newHome = total * blendedShare;
    return [Math.round(newHome * 10) / 10, Math.round((total - newHome) * 10) / 10];
  }
  if (BUMP_CATS.includes(category)) {
    const gap = Math.abs(expectedScore - 0.5) * 2;
    const maxBump = category === "yellow_cards" ? cardBumpMax : foulBumpMax;
    const bump = gap * maxBump * eloTrust;
    if (expectedScore > 0.5) {
      return [Math.round(homeEst * 10) / 10, Math.round((awayEst + bump) * 10) / 10];
    }
    return [Math.round((homeEst + bump) * 10) / 10, Math.round(awayEst * 10) / 10];
  }
  return [Math.round(homeEst * 10) / 10, Math.round(awayEst * 10) / 10];
}

function recomputeMatch(m, weights) {
  const catLabels = ["shots", "shots_on_target", "fouls", "corners", "offside", "yellow_cards"];
  const recomputed = {};
  const DERBY_BOOST_CATEGORIES = ["fouls", "yellow_cards", "corners"];
  for (const category of catLabels) {
    const c = m.components && m.components[category];
    if (!c) continue;
    const refWeight = c.is_referee_boosted ? weights.referee_boosted : weights.referee;
    let homeEst = combineEstimateJS(c.home_form, c.home_own, c.home_opp, c.referee, weights, refWeight);
    let awayEst = combineEstimateJS(c.away_form, c.away_own, c.away_opp, c.referee, weights, refWeight);
    [homeEst, awayEst] = applyEloAdjustmentJS(
      category, homeEst, awayEst, m.expected_score, m.elo_trust,
      weights.elo_split, weights.elo_foul_bump_max, weights.elo_card_bump_max
    );
    if (m.is_derby && DERBY_BOOST_CATEGORIES.includes(category) && homeEst !== null && awayEst !== null) {
      const boost = 1 + weights.derby_boost;
      homeEst = Math.round(homeEst * boost * 10) / 10;
      awayEst = Math.round(awayEst * boost * 10) / 10;
    }
    recomputed[`home_${category}`] = homeEst;
    recomputed[`away_${category}`] = awayEst;
    if (homeEst !== null && awayEst !== null) {
      recomputed[`total_${category}`] = Math.round((homeEst + awayEst) * 10) / 10;
    }
  }
  return recomputed;
}

function getCurrentWeights() {
  return {
    form: parseFloat(document.getElementById("w-form").value),
    own_venue: parseFloat(document.getElementById("w-venue").value),
    opponent_other_venue: parseFloat(document.getElementById("w-defense").value),
    referee: parseFloat(document.getElementById("w-referee").value),
    referee_boosted: parseFloat(document.getElementById("w-referee-boosted").value),
    elo_split: parseFloat(document.getElementById("w-elo").value),
    derby_boost: parseFloat(document.getElementById("w-derby").value),
    elo_foul_bump_max: modelConfig ? modelConfig.elo_foul_bump_max : 3.0,
    elo_card_bump_max: modelConfig ? modelConfig.elo_card_bump_max : 1.0,
  };
}

let lastLoadedMatches = null;

function refreshUpcomingWithWeights() {
  if (!lastLoadedMatches) return;
  const weights = getCurrentWeights();
  const recomputedMatches = lastLoadedMatches.map((m) => ({ ...m, ...recomputeMatch(m, weights) }));
  renderUpcoming(recomputedMatches);
  updateSliderLabels(weights);
}

function updateSliderLabels(weights) {
  document.getElementById("w-form-label").textContent = Math.round(weights.form * 100) + "%";
  document.getElementById("w-venue-label").textContent = Math.round(weights.own_venue * 100) + "%";
  document.getElementById("w-defense-label").textContent = Math.round(weights.opponent_other_venue * 100) + "%";
  document.getElementById("w-referee-label").textContent = Math.round(weights.referee * 100) + "%";
  document.getElementById("w-referee-boosted-label").textContent = Math.round(weights.referee_boosted * 100) + "%";
  document.getElementById("w-elo-label").textContent = Math.round(weights.elo_split * 100) + "%";
  document.getElementById("w-derby-label").textContent = "+" + Math.round(weights.derby_boost * 100) + "%";
}

function initSliders() {
  if (!modelConfig) return;
  document.getElementById("w-form").value = modelConfig.weights.form;
  document.getElementById("w-venue").value = modelConfig.weights.own_venue;
  document.getElementById("w-defense").value = modelConfig.weights.opponent_other_venue;
  document.getElementById("w-referee").value = modelConfig.weights.referee;
  document.getElementById("w-referee-boosted").value = modelConfig.referee_weight_overrides.fouls || 0.35;
  document.getElementById("w-elo").value = modelConfig.elo_split_weight;
  document.getElementById("w-derby").value = modelConfig.derby_boost_pct || 0.20;

  document.querySelectorAll(".weight-slider").forEach((slider) => {
    slider.addEventListener("input", refreshUpcomingWithWeights);
  });
  updateSliderLabels(getCurrentWeights());
}

function resetSliders() {
  initSliders();
  refreshUpcomingWithWeights();
}

function renderAdvancedTable(rows) {
  const body = document.getElementById("advanced-table-body");
  body.innerHTML = "";
  if (!rows || rows.length === 0) {
    body.innerHTML = `<tr><td colspan="15">Δεν υπάρχουν δεδομένα ακόμα.</td></tr>`;
    return;
  }
  rows.sort((a, b) => a.rank - b.rank);
  for (const r of rows) {
    const tr = document.createElement("tr");
    const fmt = (v, suffix = "") => (v === null || v === undefined ? "-" : v + suffix);
    tr.innerHTML = `
      <td>${r.rank}</td><td>${r.team}</td>
      <td>${fmt(r.goals_for)}</td><td>${fmt(r.goals_against)}</td>
      <td>${r.rating > 0 ? "+" : ""}${fmt(r.rating)}</td>
      <td>${fmt(r.attack)}</td><td>${fmt(r.defence)}</td><td>${fmt(r.openness)}</td>
      <td>${fmt(r.shot_volume)}</td><td>${fmt(r.on_target_pct, "%")}</td>
      <td>${fmt(r.possession_pct, "%")}</td><td>${fmt(r.xg_per_shot)}</td>
      <td>${fmt(r.finishing)}</td><td>${fmt(r.xg_for)}</td><td>${fmt(r.xg_against)}</td>
      <td>${r.goals_minus_xg > 0 ? "+" : ""}${fmt(r.goals_minus_xg)}</td>
    `;
    body.appendChild(tr);
  }
}

async function showAdvanced() {
  const rows = await loadJson(`data/${currentLeague}_advanced.json`);
  renderAdvancedTable(rows);
  document.getElementById("team-view").style.display = "none";
  document.getElementById("elo-view").style.display = "none";
  document.getElementById("form-view").style.display = "none";
  document.getElementById("referees-view").style.display = "none";
  document.getElementById("upcoming-view").style.display = "none";
  document.getElementById("advanced-view").style.display = "none";
  document.getElementById("advanced-view").style.display = "block";
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
  document.getElementById("advanced-view").style.display = "none";
}

async function showForm() {
  const form = await loadJson(`data/${currentLeague}_form.json`);
  renderTeamTable(form, "form-table-body");
  document.getElementById("team-view").style.display = "none";
  document.getElementById("elo-view").style.display = "none";
  document.getElementById("form-view").style.display = "block";
  document.getElementById("referees-view").style.display = "none";
  document.getElementById("upcoming-view").style.display = "none";
  document.getElementById("advanced-view").style.display = "none";
}

async function showReferees() {
  const referees = await loadJson(`data/${currentLeague}_referees.json`);
  renderRefereeTable(referees);
  document.getElementById("team-view").style.display = "none";
  document.getElementById("elo-view").style.display = "none";
  document.getElementById("form-view").style.display = "none";
  document.getElementById("referees-view").style.display = "block";
  document.getElementById("upcoming-view").style.display = "none";
  document.getElementById("advanced-view").style.display = "none";
}

async function showElo() {
  const elo = await loadJson("data/elo_ratings.json");
  renderEloTable(elo);
  document.getElementById("team-view").style.display = "none";
  document.getElementById("form-view").style.display = "none";
  document.getElementById("referees-view").style.display = "none";
  document.getElementById("upcoming-view").style.display = "none";
  document.getElementById("advanced-view").style.display = "none";
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
    } else if (btn.dataset.view === "advanced") {
      showAdvanced();
    } else if (btn.dataset.league) {
      showLeague(btn.dataset.league);
    }
  });
});

showLeague("bundesliga");
showLastUpdated();
