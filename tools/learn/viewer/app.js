/*
 * LearnHarness Local Viewer Application (WP2)
 * Adapted from amosblomqvist/learnharness (components/home.tsx, sidebar.tsx,
 * chat.tsx, plan-block.tsx, graph-view.tsx, graph-canvas.ts, chain-table.tsx,
 * confidence-rail.tsx, answered-card.tsx, practice-cards.tsx, activity-dock.tsx,
 * artifacts/demo-card.tsx).
 * License: AGPL-3.0 (private workspace adaptation; do not publish).
 */

(function () {
  "use strict";

  const state = {
    activeTab: "sessions",
    theme: localStorage.getItem("lh_viewer_theme") || "dark",
    sessions: [],
    activeSessionId: null,
    activeSessionData: null,
    planData: null,
    planFingerprint: "",
    planPollTimer: null,
    planViewport: { x: 20, y: 20, k: 1 },
    graphData: { nodes: [], chains: [], positions: {}, coverage: {} },
    graphPositions: {},
    graphViewport: { x: 40, y: 40, k: 1 },
    graphMode: "canvas",
    selectedNodeId: null,
    practiceData: { attempts: [], problems: [] },
    jobsData: { jobs: [], demos: [] },
    activeDemoHtml: "",
    activeDemoTitle: "",
  };

  // --- Confidence Rail 6-step ramp (from learnharness-ref/components/confidence-rail.tsx) ---
  function confidenceColor(value) {
    const v = Number(value);
    if (!Number.isFinite(v) || v <= 15) return "#ef4444"; // 0
    if (v <= 40) return "#f97316"; // 30
    if (v <= 60) return "#eab308"; // 50
    if (v <= 80) return "#84cc16"; // 70
    if (v <= 95) return "#22c55e"; // 90
    return "#10b981"; // 100
  }

  function renderConfidenceRail(value) {
    if (value === undefined || value === null || value === "") {
      return '<span class="badge">conf: n/a</span>';
    }
    const pct = Math.max(0, Math.min(100, Math.round(Number(value) || 0)));
    const color = confidenceColor(pct);
    return (
      '<div class="confidence-rail-wrap" title="Self-reported confidence: ' +
      pct +
      '%">' +
      '<div class="confidence-rail-track">' +
      '<div class="confidence-rail-fill" style="width:' +
      pct +
      "%;background:" +
      color +
      ';"></div>' +
      "</div>" +
      '<span class="confidence-rail-label" style="color:' +
      color +
      ';">' +
      pct +
      "%</span>" +
      "</div>"
    );
  }

  function esc(str) {
    if (str === undefined || str === null) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return String(iso);
      return d.toISOString().replace("T", " ").slice(0, 19);
    } catch (_) {
      return String(iso);
    }
  }

  async function apiGet(path) {
    const res = await fetch(path, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error("HTTP " + res.status + " on " + path);
    return await res.json();
  }

  // --- Theme Management ---
  function applyTheme(theme) {
    state.theme = theme === "light" ? "light" : "dark";
    localStorage.setItem("lh_viewer_theme", state.theme);
    const root = document.documentElement;
    root.setAttribute("data-theme", state.theme);
    if (state.theme === "dark") {
      root.classList.add("dark");
    } else {
      root.classList.remove("dark");
    }
    const btn = document.getElementById("btn-theme-toggle");
    if (btn) {
      btn.innerHTML = state.theme === "dark" ? "&#x263E; Dark" : "&#x2600; Light";
    }
  }

  // --- Tab & Hash Navigation ---
  function switchTab(tabName, updateHash) {
    const valid = ["sessions", "session", "plan", "graph", "practice", "jobs"];
    const target = valid.includes(tabName) ? tabName : "sessions";
    state.activeTab = target;

    document.querySelectorAll(".nav-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-tab") === target);
    });
    document.querySelectorAll(".view-pane").forEach((pane) => {
      pane.classList.toggle("active", pane.id === "view-" + target);
    });

    if (updateHash !== false) {
      const hash = "#" + target + (state.activeSessionId && (target === "session" || target === "plan") ? "/" + state.activeSessionId : "");
      if (window.location.hash !== hash) {
        history.replaceState(null, "", hash);
      }
    }

    if (target === "session" && state.activeSessionId) {
      loadSessionDetail(state.activeSessionId);
    } else if (target === "plan") {
      loadPlan(state.activeSessionId, true);
    } else if (target === "graph") {
      loadGraph();
    } else if (target === "practice") {
      loadPractice(document.getElementById("practice-session-select").value || state.activeSessionId || "all");
    } else if (target === "jobs") {
      loadJobs();
    }
  }

  function updateActiveSessionPill() {
    const pill = document.getElementById("active-session-pill");
    if (!pill) return;
    const s = state.sessions.find((x) => x.id === state.activeSessionId);
    if (s) {
      pill.style.display = "inline-flex";
      pill.textContent = s.id + " · " + (s.topic || s.title || "Session");
    } else if (state.activeSessionId) {
      pill.style.display = "inline-flex";
      pill.textContent = state.activeSessionId;
    } else {
      pill.style.display = "none";
    }
  }

  function populateSessionDropdowns() {
    const ids = ["session-picker-select", "plan-session-select", "graph-filter-session", "practice-session-select"];
    ids.forEach((domId) => {
      const sel = document.getElementById(domId);
      if (!sel) return;
      const keepAll = domId === "graph-filter-session" || domId === "practice-session-select";
      const prev = sel.value;
      let html = keepAll ? '<option value="all">All sessions</option>' : "";
      state.sessions.forEach((s) => {
        const label = s.id + " — " + (s.topic || s.title || "Untitled") + " [" + (s.track || "general") + "]";
        html += '<option value="' + esc(s.id) + '">' + esc(label) + "</option>";
      });
      sel.innerHTML = html;
      if (prev && Array.from(sel.options).some((o) => o.value === prev)) {
        sel.value = prev;
      } else if (!keepAll && state.activeSessionId) {
        sel.value = state.activeSessionId;
      }
    });
  }

  // --- 1. SESSIONS VIEW ---
  function normalizeSessionItem(raw) {
    const planSteps = (raw.plan && (raw.plan.steps || raw.plan.nodes)) || (raw.tree && raw.tree.nodes) || [];
    const doneSteps = planSteps.filter((st) => st.status === "done").length;
    const currentObj = planSteps.find((st) => st.status === "current");
    return {
      id: raw.id || raw.sessionId || raw.chatId || "unknown",
      title: raw.title || raw.topic || raw.id || "Untitled Session",
      topic: raw.topic || raw.title || "",
      goal: raw.goal || "",
      track: raw.track || "general",
      status: raw.status || (raw.closedAt ? "closed" : "open"),
      createdAt: raw.createdAt || "",
      updatedAt: raw.updatedAt || raw.createdAt || "",
      links: raw.links || raw.linkedSessions || [],
      stepCount: raw.stepCount !== undefined ? raw.stepCount : planSteps.length,
      doneCount: raw.doneCount !== undefined ? raw.doneCount : doneSteps,
      currentStep: raw.currentStep || (currentObj ? currentObj.id : ""),
      cardCount: raw.cardCount !== undefined ? raw.cardCount : (raw.events ? raw.events.length : (raw.messages ? raw.messages.length : 0)),
    };
  }

  async function loadSessions() {
    try {
      const raw = await apiGet("/api/sessions");
      const list = Array.isArray(raw) ? raw : raw.sessions || raw.chats || [];
      state.sessions = list.map(normalizeSessionItem);
      if (!state.activeSessionId && state.sessions.length > 0) {
        const openOne = state.sessions.find((s) => s.status === "open") || state.sessions[0];
        state.activeSessionId = openOne.id;
      }
      document.getElementById("badge-sessions").textContent = String(state.sessions.length);
      document.getElementById("stat-total-sessions").textContent = String(state.sessions.length);
      updateActiveSessionPill();
      populateSessionDropdowns();
      renderSessionsList();
    } catch (err) {
      document.getElementById("sessions-container").innerHTML =
        '<div class="empty-state">Could not load /api/sessions: ' + esc(err.message) + "</div>";
    }
  }

  function renderSessionsList() {
    const trackFilter = document.getElementById("filter-session-track").value;
    const statusFilter = document.getElementById("filter-session-status").value;
    const container = document.getElementById("sessions-container");

    const filtered = state.sessions.filter((s) => {
      if (trackFilter !== "all" && s.track !== trackFilter) return false;
      if (statusFilter !== "all" && s.status !== statusFilter) return false;
      return true;
    });

    if (filtered.length === 0) {
      container.innerHTML = '<div class="empty-state">No sessions match the selected filters.</div>';
      return;
    }

    container.innerHTML = filtered
      .map((s) => {
        const trackClass = "badge-" + (["internship", "thesis", "general"].includes(s.track) ? s.track : "general");
        const statusClass = s.status === "open" ? "badge-open" : "badge-closed";
        const linksHtml =
          s.links && s.links.length
            ? '<div class="session-links-row"><span style="font-size:11px;color:var(--muted-foreground);">Linked:</span>' +
              s.links
                .map(
                  (lid) =>
                    '<button type="button" class="link-pill" data-open-session="' +
                    esc(lid) +
                    '">&#x1F517; ' +
                    esc(lid) +
                    "</button>"
                )
                .join("") +
              "</div>"
            : "";

        return (
          '<article class="session-card ' +
          (s.id === state.activeSessionId ? "selected" : "") +
          '" data-session-id="' +
          esc(s.id) +
          '">' +
          "<div>" +
          '<div class="session-card-header">' +
          '<div style="display:flex;gap:6px;flex-wrap:wrap;">' +
          '<span class="badge ' +
          trackClass +
          '">' +
          esc(s.track) +
          "</span>" +
          '<span class="badge ' +
          statusClass +
          '">' +
          esc(s.status) +
          "</span>" +
          '<span class="badge">' +
          esc(s.id) +
          "</span>" +
          "</div>" +
          '<span class="badge">' +
          esc(s.doneCount) +
          "/" +
          esc(s.stepCount) +
          " steps</span>" +
          "</div>" +
          '<h3 class="session-title" style="margin-top:8px;" data-open-session="' +
          esc(s.id) +
          '">' +
          esc(s.topic || s.title) +
          "</h3>" +
          (s.goal ? '<p class="session-goal">Goal: ' + esc(s.goal) + "</p>" : "") +
          "</div>" +
          '<div style="display:flex;flex-direction:column;gap:8px;">' +
          linksHtml +
          '<div class="session-meta">' +
          "<span>Updated: " +
          esc(fmtTime(s.updatedAt)) +
          "</span>" +
          (s.currentStep ? '<span>&bull; Current: <strong>' + esc(s.currentStep) + "</strong></span>" : "") +
          "</div>" +
          '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;">' +
          '<button type="button" class="btn btn-sm btn-brand" data-open-session="' +
          esc(s.id) +
          '">Open</button>' +
          '<button type="button" class="btn btn-sm" data-plan-session="' +
          esc(s.id) +
          '">Plan Map</button>' +
          '<button type="button" class="btn btn-sm" data-continue-session="' +
          esc(s.id) +
          '">Continue</button>' +
          '<button type="button" class="btn btn-sm" data-link-session="' +
          esc(s.id) +
          '">Link</button>' +
          "</div>" +
          "</div>" +
          "</article>"
        );
      })
      .join("");
  }

  // --- Global Search (/api/search?q=) ---
  async function runGlobalSearch() {
    const q = document.getElementById("global-search-input").value.trim();
    const scope = document.getElementById("global-search-scope").value;
    const panel = document.getElementById("search-results-panel");
    const listEl = document.getElementById("search-results-list");
    const clearBtn = document.getElementById("btn-clear-search");

    if (!q) {
      panel.style.display = "none";
      clearBtn.style.display = "none";
      return;
    }

    clearBtn.style.display = "inline-flex";
    panel.style.display = "block";
    listEl.innerHTML = '<div style="font-size:12px;color:var(--muted-foreground);">Searching...</div>';

    try {
      const res = await apiGet("/api/search?q=" + encodeURIComponent(q) + "&in=" + encodeURIComponent(scope));
      const hits = Array.isArray(res) ? res : res.hits || res.results || [];
      document.getElementById("search-results-title").textContent =
        'Search Results for "' + q + '" (' + hits.length + ")";

      if (hits.length === 0) {
        listEl.innerHTML = '<div style="font-size:13px;color:var(--muted-foreground);padding:8px 0;">No matches found.</div>';
        return;
      }

      listEl.innerHTML = hits
        .map((h) => {
          const kind = h.kind || h.scope || h.type || "hit";
          const sid = h.sessionId || h.session || h.chatId || h.id || "";
          const step = h.step || "";
          const snippet = h.snippet || h.statement || h.title || h.text || "";
          const nodeId = h.nodeId || (kind === "node" || kind === "nodes" ? h.id : "");
          return (
            '<div class="search-hit" data-hit-session="' +
            esc(sid) +
            '" data-hit-node="' +
            esc(nodeId) +
            '" data-hit-kind="' +
            esc(kind) +
            '">' +
            '<div style="flex:1;">' +
            '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">' +
            '<span class="badge badge-internship">' +
            esc(kind) +
            "</span>" +
            (sid ? '<span class="badge">' + esc(sid) + "</span>" : "") +
            (step ? '<span class="badge">step: ' + esc(step) + "</span>" : "") +
            (nodeId ? '<span class="badge">' + esc(nodeId) + "</span>" : "") +
            "</div>" +
            '<div class="search-snippet">' +
            esc(snippet) +
            "</div>" +
            "</div>" +
            '<span style="font-family:var(--font-mono);font-size:11px;color:var(--brand);">Open &rarr;</span>' +
            "</div>"
          );
        })
        .join("");
    } catch (err) {
      listEl.innerHTML = '<div style="color:var(--danger);font-size:12px;">Search error: ' + esc(err.message) + "</div>";
    }
  }

  // --- 2. SESSION VIEW (#session) ---
  function extractCardsFromSession(raw) {
    const s = raw.session || raw;
    const explicit = raw.events || raw.cards || s.events || s.cards || [];
    if (Array.isArray(explicit) && explicit.length > 0) {
      return explicit;
    }
    // Fallback: synthesize card records from native learnharness messages (toolResult / custom)
    const msgs = raw.messages || s.messages || [];
    const cards = [];
    msgs.forEach((m, idx) => {
      if (m.customType === "learn-card" || m.kind === "card" || m.payload) {
        const p = m.payload || m.details || m;
        cards.push({
          id: m.id || "card_" + idx,
          step: p.step || m.step || "",
          kind: p.kind || m.cardKind || "note",
          question: p.question || p.prompt || m.content || "",
          think1: p.think1 || p.thinking || "",
          think2: p.think2 || "",
          think3: p.think3 || "",
          q1: p.q1 || p.answer || p.text || "",
          q2: p.q2 || "",
          choice: p.choice || "",
          confidence: p.confidence,
          score: p.score,
          feedback: p.feedback || "",
          timestamp: m.timestamp || m.createdAt || "",
        });
      } else if (m.role === "toolResult" && m.details) {
        const d = m.details;
        cards.push({
          id: m.id || "tr_" + idx,
          step: d.step || "",
          kind: d.questionType || d.tool || d.kind || "check",
          question: d.questionText || d.statement || "",
          think1: d.thinking || d.note || "",
          think2: d.readingThinking || "",
          q1: d.text || (Array.isArray(d.answers) ? d.answers.map((a) => a.label || a.value).join(", ") : ""),
          confidence: d.confidence,
          score: d.correct !== undefined ? (d.correct ? "correct" : "wrong") : d.score,
          feedback: d.explanation || d.feedback || "",
          timestamp: m.timestamp || "",
        });
      }
    });
    return cards;
  }

  async function loadSessionDetail(sessionId) {
    if (!sessionId) return;
    state.activeSessionId = sessionId;
    updateActiveSessionPill();
    populateSessionDropdowns();

    const headerEl = document.getElementById("session-detail-header");
    const streamEl = document.getElementById("session-cards-stream");
    headerEl.innerHTML = '<div style="color:var(--muted-foreground);">Loading session ' + esc(sessionId) + "...</div>";

    try {
      const raw = await apiGet("/api/session/" + encodeURIComponent(sessionId));
      state.activeSessionData = raw;
      renderSessionDetail();
    } catch (err) {
      headerEl.innerHTML = '<div class="empty-state">Error loading session: ' + esc(err.message) + "</div>";
      streamEl.innerHTML = "";
    }
  }

  function renderSessionDetail() {
    const raw = state.activeSessionData;
    if (!raw) return;
    const s = raw.session || raw;
    const sid = s.id || state.activeSessionId;
    const links = s.links || s.linkedSessions || raw.links || [];
    const track = s.track || "general";
    const status = s.status || "open";

    const linksHtml =
      links.length > 0
        ? links
            .map(
              (lid) =>
                '<button type="button" class="link-pill" data-open-session="' +
                esc(lid) +
                '">&#x1F517; ' +
                esc(lid) +
                "</button>"
            )
            .join(" ")
        : '<span style="font-size:12px;color:var(--muted-foreground);">None linked yet</span>';

    document.getElementById("session-detail-header").innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;">' +
      "<div>" +
      '<div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;flex-wrap:wrap;">' +
      '<span class="badge badge-' +
      esc(track) +
      '">' +
      esc(track) +
      "</span>" +
      '<span class="badge badge-' +
      (status === "open" ? "open" : "closed") +
      '">' +
      esc(status) +
      "</span>" +
      '<span class="badge">' +
      esc(sid) +
      "</span>" +
      '<span style="font-family:var(--font-mono);font-size:11px;color:var(--muted-foreground);">Updated: ' +
      esc(fmtTime(s.updatedAt || s.createdAt)) +
      "</span>" +
      "</div>" +
      '<h2 style="margin:0;font-family:var(--font-serif);font-size:22px;">' +
      esc(s.topic || s.title || sid) +
      "</h2>" +
      (s.goal ? '<p style="margin:4px 0 0 0;color:var(--muted-foreground);"><strong>Goal:</strong> ' + esc(s.goal) + "</p>" : "") +
      "</div>" +
      '<div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px;">' +
      '<div class="session-links-row"><strong>Linked Sessions:</strong> ' +
      linksHtml +
      "</div>" +
      "</div>" +
      "</div>";

    const cards = extractCardsFromSession(raw);
    const findQuery = (document.getElementById("session-find-input").value || "").trim().toLowerCase();
    const kindFilter = document.getElementById("session-kind-filter").value;

    const filteredCards = cards.filter((c) => {
      const k = (c.kind || "note").toLowerCase();
      if (kindFilter !== "all" && k !== kindFilter) return false;
      if (findQuery) {
        const hay = JSON.stringify(c).toLowerCase();
        if (!hay.includes(findQuery)) return false;
      }
      return true;
    });

    const streamEl = document.getElementById("session-cards-stream");
    if (filteredCards.length === 0) {
      streamEl.innerHTML =
        '<div class="empty-state">No logged card events (' +
        (cards.length ? "matching filter" : "predict / reading / check / probe / practice") +
        ") in this session yet.</div>";
    } else {
      streamEl.innerHTML = filteredCards
        .map((c, i) => {
          const p = c.payload || c;
          const kind = (c.kind || p.kind || "note").toLowerCase();
          const step = c.step || p.step || "#" + (i + 1);
          const conf = c.confidence !== undefined ? c.confidence : p.confidence;
          const score = c.score !== undefined ? c.score : p.score !== undefined ? p.score : p.result;
          const feedback = c.feedback || p.feedback || "";
          const question = c.question || p.question || p.prompt || p.problem || "";

          const fields = [];
          ["think1", "think2", "think3", "thinking", "readingThinking", "q1", "q2", "answer", "choice", "hints", "note"].forEach(
            (k) => {
              const val = c[k] !== undefined ? c[k] : p[k];
              if (val !== undefined && val !== null && String(val).trim() !== "") {
                fields.push({ label: k, value: val });
              }
            }
          );

          return (
            '<div class="turn-card" data-kind="' +
            esc(kind) +
            '">' +
            '<div class="turn-header">' +
            '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">' +
            '<span class="badge">' +
            esc(step) +
            "</span>" +
            '<span class="badge badge-internship">' +
            esc(kind.toUpperCase()) +
            "</span>" +
            (score !== undefined && score !== null && score !== ""
              ? '<span class="badge badge-open">Score: ' + esc(score) + "</span>"
              : "") +
            "</div>" +
            '<div style="display:flex;gap:10px;align-items:center;">' +
            renderConfidenceRail(conf) +
            '<span style="font-family:var(--font-mono);font-size:11px;color:var(--muted-foreground);">' +
            esc(fmtTime(c.timestamp || c.createdAt || p.timestamp)) +
            "</span>" +
            "</div>" +
            "</div>" +
            (question ? '<div style="font-weight:600;margin-bottom:8px;">' + esc(question) + "</div>" : "") +
            (fields.length
              ? '<div class="field-grid">' +
                fields
                  .map(
                    (f) =>
                      '<div class="field-box"><div class="field-label">' +
                      esc(f.label) +
                      '</div><div class="field-value">' +
                      esc(typeof f.value === "object" ? JSON.stringify(f.value) : f.value) +
                      "</div></div>"
                  )
                  .join("") +
                "</div>"
              : "") +
            (feedback ? '<div class="feedback-box"><strong>Teacher Feedback:</strong> ' + esc(feedback) + "</div>" : "") +
            "</div>"
          );
        })
        .join("");
    }

    // Raw messages section
    const rawMsgs = raw.messages || s.messages || [];
    document.getElementById("raw-messages-count").textContent = String(rawMsgs.length);
    document.getElementById("session-raw-messages").innerHTML = rawMsgs
      .map((m) => {
        const role = m.role || m.customType || "message";
        const body =
          typeof m.content === "string"
            ? m.content
            : JSON.stringify(m.content || m.details || m.payload || m, null, 2);
        return (
          '<div class="field-box">' +
          '<div class="field-label">' +
          esc(role) +
          " &bull; " +
          esc(m.id || "") +
          "</div>" +
          '<div class="field-value" style="font-family:var(--font-mono);font-size:12px;">' +
          esc(body) +
          "</div>" +
          "</div>"
        );
      })
      .join("");
  }

  // --- 3. PLAN MAP VIEW (#plan) + 1.5s AUTO-REFRESH ---
  function normalizePlan(raw) {
    const p = raw.plan || raw.tree || raw || {};
    const rawSteps = p.steps || p.nodes || raw.steps || [];
    const currentStepId = p.currentStep || raw.currentStep || "";
    const steps = rawSteps.map((st, idx) => {
      const id = st.id || "s" + (idx + 1);
      let status = (st.status || "todo").toLowerCase();
      if (currentStepId && id === currentStepId && status !== "done") {
        status = "current";
      }
      return {
        id: id,
        title: st.title || st.label || st.name || id,
        deps: st.deps || st.dependsOn || st.prerequisites || st.parents || [],
        status: status,
      };
    });
    const activeObj = steps.find((s) => s.status === "current");
    return {
      sessionId: raw.sessionId || raw.id || state.activeSessionId || "",
      updatedAt: p.updatedAt || raw.updatedAt || "",
      currentStep: currentStepId || (activeObj ? activeObj.id : ""),
      steps: steps,
      mermaid: raw.mermaid || p.mermaid || buildMermaidFromSteps(steps),
    };
  }

  function buildMermaidFromSteps(steps) {
    if (!steps || !steps.length) return "graph TD\n  empty[No plan steps]";
    const lines = ["graph TD"];
    steps.forEach((st) => {
      const tag = st.status === "current" ? " (YOU ARE HERE)" : st.status === "done" ? " (DONE)" : "";
      const safeLabel = (st.id + ": " + st.title + tag).replace(/"/g, "'");
      lines.push("  " + st.id + '["' + safeLabel + '"]');
      (st.deps || []).forEach((d) => {
        lines.push("  " + d + " --> " + st.id);
      });
    });
    return lines.join("\n");
  }

  async function loadPlan(sessionId, forceRender) {
    const sid = sessionId || document.getElementById("plan-session-select").value || state.activeSessionId;
    if (!sid) return;
    if (state.activeSessionId !== sid) {
      state.activeSessionId = sid;
      updateActiveSessionPill();
      populateSessionDropdowns();
    }

    try {
      const raw = await apiGet("/api/plan/" + encodeURIComponent(sid));
      const norm = normalizePlan(raw);
      const fp =
        norm.updatedAt +
        "|" +
        norm.currentStep +
        "|" +
        norm.steps.map((s) => s.id + ":" + s.status + ":" + s.title).join(",");

      const lastUpEl = document.getElementById("plan-last-updated");
      if (lastUpEl) {
        lastUpEl.textContent = "Checked " + new Date().toLocaleTimeString();
      }

      const doneCount = norm.steps.filter((s) => s.status === "done").length;
      document.getElementById("badge-plan").textContent = doneCount + "/" + norm.steps.length;

      if (forceRender || fp !== state.planFingerprint) {
        state.planFingerprint = fp;
        state.planData = norm;
        renderPlanView(norm);
      }
    } catch (err) {
      if (forceRender) {
        document.getElementById("plan-svg-viewport").innerHTML =
          '<div class="empty-state" style="margin:20px;">No plan set for session ' +
          esc(sid) +
          " (" +
          esc(err.message) +
          ")</div>";
      }
    }
  }

  function renderPlanView(plan) {
    const steps = plan.steps || [];
    const doneCount = steps.filter((s) => s.status === "done").length;
    document.getElementById("plan-progress-pill").textContent = doneCount + " / " + steps.length + " done";

    const currentStepObj = steps.find((s) => s.status === "current");
    const bannerEl = document.getElementById("plan-current-banner");
    if (currentStepObj) {
      bannerEl.innerHTML =
        '<span class="you-are-here-badge">&#x25CF; YOU ARE HERE: ' +
        esc(currentStepObj.id) +
        " — " +
        esc(currentStepObj.title) +
        "</span>";
    } else if (steps.length > 0 && doneCount === steps.length) {
      bannerEl.innerHTML = '<span class="badge badge-done">All Plan Steps Complete!</span>';
    } else {
      bannerEl.innerHTML = "";
    }

    // Step checklist
    const listEl = document.getElementById("plan-steps-list");
    if (steps.length === 0) {
      listEl.innerHTML = '<div class="empty-state">No steps in this plan yet.</div>';
    } else {
      listEl.innerHTML = steps
        .map((st) => {
          const rowClass = st.status === "current" ? "step-current" : st.status === "done" ? "step-done" : "";
          const badgeClass =
            st.status === "current" ? "badge-current" : st.status === "done" ? "badge-done" : "badge-todo";
          const depsText = st.deps && st.deps.length ? "Deps: " + st.deps.join(", ") : "Root step (no deps)";
          return (
            '<div class="plan-step-row ' +
            rowClass +
            '" data-step-id="' +
            esc(st.id) +
            '">' +
            "<div>" +
            '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">' +
            '<span class="badge">' +
            esc(st.id) +
            "</span>" +
            "<strong>" +
            esc(st.title) +
            "</strong>" +
            (st.status === "current" ? '<span class="you-are-here-badge">YOU ARE HERE</span>' : "") +
            "</div>" +
            '<div style="font-family:var(--font-mono);font-size:11px;color:var(--muted-foreground);margin-top:3px;">' +
            esc(depsText) +
            "</div>" +
            "</div>" +
            '<span class="badge ' +
            badgeClass +
            '">' +
            esc(st.status.toUpperCase()) +
            "</span>" +
            "</div>"
          );
        })
        .join("");
    }

    // Native SVG DAG Tree (always works offline)
    renderPlanSvgTree(steps);

    // Mermaid source & optional Mermaid render
    const mSrc = document.getElementById("plan-mermaid-source");
    const mRender = document.getElementById("plan-mermaid-render");
    if (mSrc) mSrc.textContent = plan.mermaid || "";
    if (mRender) {
      if (window.mermaid && typeof window.mermaid.render === "function" && plan.mermaid) {
        try {
          window.mermaid.initialize({ startOnLoad: false, theme: state.theme === "dark" ? "dark" : "default" });
          const uid = "mermaid_plan_" + Date.now();
          window.mermaid
            .render(uid, plan.mermaid)
            .then((res) => {
              mRender.innerHTML = res.svg;
            })
            .catch(() => {
              mRender.innerHTML =
                '<div style="font-size:12px;color:var(--muted-foreground);">Rendered via native SVG DAG above.</div>';
            });
        } catch (_) {
          mRender.innerHTML =
            '<div style="font-size:12px;color:var(--muted-foreground);">Rendered via native SVG DAG above.</div>';
        }
      } else {
        mRender.innerHTML =
          '<div style="font-size:12px;color:var(--muted-foreground);">Native SVG DAG active above (Mermaid source below).</div>';
      }
    }
  }

  function renderPlanSvgTree(steps) {
    const viewport = document.getElementById("plan-svg-viewport");
    if (!steps || steps.length === 0) {
      viewport.innerHTML = '<div class="empty-state" style="margin:20px;">No plan steps to visualize.</div>';
      return;
    }

    // Compute topological level for each step
    const byId = {};
    steps.forEach((s) => {
      byId[s.id] = s;
    });
    const memo = {};
    function getDepth(id, stack) {
      if (memo[id] !== undefined) return memo[id];
      if (stack.has(id)) return 0;
      stack.add(id);
      const st = byId[id];
      if (!st || !st.deps || !st.deps.length) {
        memo[id] = 0;
        return 0;
      }
      let maxD = 0;
      st.deps.forEach((d) => {
        if (byId[d]) {
          maxD = Math.max(maxD, getDepth(d, stack) + 1);
        }
      });
      memo[id] = maxD;
      return maxD;
    }
    steps.forEach((s) => getDepth(s.id, new Set()));

    const layers = {};
    steps.forEach((s) => {
      const lv = memo[s.id] || 0;
      if (!layers[lv]) layers[lv] = [];
      layers[lv].push(s);
    });

    const nodeW = 220;
    const nodeH = 68;
    const gapX = 48;
    const gapY = 110;
    const coords = {};

    Object.keys(layers).forEach((lvStr) => {
      const lv = Number(lvStr);
      const row = layers[lv];
      row.forEach((st, idx) => {
        const totalW = row.length * nodeW + (row.length - 1) * gapX;
        const startX = Math.max(40, (680 - totalW) / 2);
        coords[st.id] = {
          x: startX + idx * (nodeW + gapX),
          y: 36 + lv * gapY,
        };
      });
    });

    let edgesSvg = "";
    steps.forEach((st) => {
      const to = coords[st.id];
      (st.deps || []).forEach((depId) => {
        const from = coords[depId];
        if (!from || !to) return;
        const x1 = from.x + nodeW / 2;
        const y1 = from.y + nodeH;
        const x2 = to.x + nodeW / 2;
        const y2 = to.y;
        const cy = (y1 + y2) / 2;
        const edgeColor = st.status === "done" ? "#22c55e" : st.status === "current" ? "#f59e0b" : "#64748b";
        edgesSvg +=
          '<path d="M ' +
          x1 +
          " " +
          y1 +
          " C " +
          x1 +
          " " +
          cy +
          ", " +
          x2 +
          " " +
          cy +
          ", " +
          x2 +
          " " +
          (y2 - 6) +
          '" fill="none" stroke="' +
          edgeColor +
          '" stroke-width="2" marker-end="url(#plan-arrow)" />';
      });
    });

    let nodesSvg = "";
    steps.forEach((st) => {
      const pt = coords[st.id];
      const isCurrent = st.status === "current";
      const isDone = st.status === "done";
      const stroke = isCurrent ? "#f59e0b" : isDone ? "#22c55e" : "#52525b";
      const fill = isCurrent ? "rgba(245, 158, 11, 0.16)" : isDone ? "rgba(34, 197, 94, 0.14)" : "#1c1c1c";
      const statusLabel = isCurrent ? "● YOU ARE HERE" : isDone ? "✓ DONE" : "TODO";
      const statusColor = isCurrent ? "#f59e0b" : isDone ? "#22c55e" : "#94a3b8";
      const shortTitle = st.title.length > 26 ? st.title.slice(0, 25) + "…" : st.title;

      nodesSvg +=
        '<g class="plan-svg-node" transform="translate(' +
        pt.x +
        "," +
        pt.y +
        ')">' +
        '<rect width="' +
        nodeW +
        '" height="' +
        nodeH +
        '" rx="8" fill="' +
        fill +
        '" stroke="' +
        stroke +
        '" stroke-width="' +
        (isCurrent ? "3" : "1.5") +
        '" />' +
        '<text x="12" y="22" fill="' +
        statusColor +
        '" font-family="JetBrains Mono, monospace" font-size="10" font-weight="700">' +
        esc(st.id.toUpperCase() + " · " + statusLabel) +
        "</text>" +
        '<text x="12" y="45" fill="#e6e6e6" font-family="Source Sans 3, sans-serif" font-size="13" font-weight="600">' +
        esc(shortTitle) +
        "</text>" +
        "<title>" +
        esc(st.id + ": " + st.title + " (" + st.status + ")") +
        "</title>" +
        "</g>";
    });

    const vp = state.planViewport;
    viewport.innerHTML =
      '<svg width="100%" height="100%" viewBox="0 0 760 460">' +
      "<defs>" +
      '<marker id="plan-arrow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">' +
      '<path d="M 0 1 L 10 5 L 0 9 z" fill="#8d8d8d" />' +
      "</marker>" +
      "</defs>" +
      '<g id="plan-svg-root" transform="translate(' +
      vp.x +
      "," +
      vp.y +
      ") scale(" +
      vp.k +
      ')">' +
      edgesSvg +
      nodesSvg +
      "</g>" +
      "</svg>";

    attachSvgPanZoom(viewport, state.planViewport, "plan-svg-root");
  }

  function startPlanAutoRefresh() {
    if (state.planPollTimer) clearInterval(state.planPollTimer);
    state.planPollTimer = setInterval(() => {
      if (state.activeSessionId && (state.activeTab === "plan" || state.activeTab === "session")) {
        loadPlan(state.activeSessionId, false);
      }
    }, 1500);
  }

  // --- 4. GRAPH VIEW (#graph) ---
  function computeGraphDepthAndCoverage(raw) {
    const nodesRaw = raw.nodes || [];
    const chainsRaw = raw.chains || raw.edges || [];
    const posRaw = (raw.positions && raw.positions.positions) || raw.positions || {};
    const covRaw = raw.coverage || [];

    // Coverage map by nodeId
    const covMap = {};
    if (Array.isArray(covRaw)) {
      covRaw.forEach((c) => {
        const nid = c.nodeId || c.node || c.id;
        if (!nid) return;
        if (c.status || c.coverage) {
          covMap[nid] = c.status || c.coverage;
        } else if (c.result === "correct") {
          covMap[nid] = "mastered";
        } else if (c.result === "partial" || c.result === "wrong") {
          if (covMap[nid] !== "mastered") covMap[nid] = "shaky";
        }
      });
    } else if (covRaw && typeof covRaw === "object") {
      Object.assign(covMap, covRaw);
    }

    const byId = {};
    nodesRaw.forEach((n) => {
      const prov = Array.isArray(n.provenance) ? n.provenance : [];
      const sessions = new Set();
      if (n.session) sessions.add(n.session);
      if (n.chatId) sessions.add(n.chatId);
      prov.forEach((p) => {
        if (p.session) sessions.add(p.session);
        if (p.chatId) sessions.add(p.chatId);
      });
      const cov = (n.coverage || n.status || covMap[n.id] || "unseen").toLowerCase();
      byId[n.id] = {
        id: n.id,
        name: n.name || n.title || n.id,
        statement: n.statement || n.text || "",
        aliases: n.aliases || [],
        depth: typeof n.depth === "number" ? n.depth : null,
        coverage: ["mastered", "shaky", "unseen"].includes(cov) ? cov : "unseen",
        track: n.track || "general",
        sessions: Array.from(sessions),
        provenance: prov,
        practiceStats: n.practiceStats || null,
      };
    });

    // Compute longest chain depth from root premises if not provided
    const parentsOf = {};
    chainsRaw.forEach((ch) => {
      const concl = ch.conclusion || ch.to || ch.target;
      const prems = ch.premises || (ch.from ? [ch.from] : []);
      if (!concl) return;
      if (!parentsOf[concl]) parentsOf[concl] = [];
      prems.forEach((p) => {
        if (p && !parentsOf[concl].includes(p)) parentsOf[concl].push(p);
      });
    });

    const depthMemo = {};
    function calcDepth(nid, visiting) {
      if (depthMemo[nid] !== undefined) return depthMemo[nid];
      if (visiting.has(nid)) return 0;
      visiting.add(nid);
      const pars = parentsOf[nid] || [];
      if (!pars.length) {
        depthMemo[nid] = 0;
        return 0;
      }
      let d = 0;
      pars.forEach((p) => {
        d = Math.max(d, calcDepth(p, visiting) + 1);
      });
      depthMemo[nid] = d;
      return d;
    }

    Object.keys(byId).forEach((nid) => {
      if (byId[nid].depth === null) {
        byId[nid].depth = calcDepth(nid, new Set());
      }
    });

    // Normalize chains and mark cross-session links
    const normChains = chainsRaw.map((ch, idx) => {
      const prems = ch.premises || (ch.from ? [ch.from] : []);
      const concl = ch.conclusion || ch.to || ch.target || "";
      let cross = Boolean(ch.crossSession);
      if (!cross && prems.length && concl && byId[concl]) {
        const conclSessions = byId[concl].sessions;
        prems.forEach((pid) => {
          const pNode = byId[pid];
          if (pNode && pNode.sessions.length && conclSessions.length) {
            const pPrimary = pNode.sessions[0];
            const cPrimary = conclSessions[0];
            if (pPrimary && cPrimary && pPrimary !== cPrimary) {
              cross = true;
            }
          }
        });
      }
      return {
        id: ch.id || "chain_" + (idx + 1),
        premises: prems,
        conclusion: concl,
        crossSession: cross,
        provenance: ch.provenance || [],
      };
    });

    // Assign default {x, y} coordinates where Y = depth * 125
    const byDepth = {};
    Object.values(byId).forEach((n) => {
      const d = n.depth || 0;
      if (!byDepth[d]) byDepth[d] = [];
      byDepth[d].push(n);
    });

    const mergedPositions = {};
    Object.keys(byDepth).forEach((dStr) => {
      const d = Number(dStr);
      const row = byDepth[d];
      row.forEach((n, idx) => {
        const saved = posRaw[n.id] || state.graphPositions[n.id];
        if (saved && typeof saved.x === "number" && typeof saved.y === "number") {
          mergedPositions[n.id] = { x: saved.x, y: saved.y };
        } else {
          mergedPositions[n.id] = {
            x: 80 + idx * 240,
            y: 50 + d * 130,
          };
        }
      });
    });

    return {
      nodes: Object.values(byId),
      nodesById: byId,
      chains: normChains,
      positions: mergedPositions,
    };
  }

  async function loadGraph() {
    try {
      const raw = await apiGet("/api/graph");
      const processed = computeGraphDepthAndCoverage(raw);
      state.graphData = processed;
      state.graphPositions = processed.positions;

      document.getElementById("badge-graph").textContent = String(processed.nodes.length);
      document.getElementById("stat-total-nodes").textContent = String(processed.nodes.length);
      document.getElementById("stat-total-chains").textContent = String(processed.chains.length);
      const mastered = processed.nodes.filter((n) => n.coverage === "mastered").length;
      const shaky = processed.nodes.filter((n) => n.coverage === "shaky").length;
      document.getElementById("stat-mastery-ratio").textContent = mastered + " / " + shaky;

      renderGraph();
    } catch (err) {
      document.getElementById("graph-svg-container").innerHTML =
        '<div class="empty-state" style="margin:20px;">Could not load /api/graph: ' + esc(err.message) + "</div>";
    }
  }

  function getFilteredGraphNodes() {
    const sessFilter = document.getElementById("graph-filter-session").value;
    const trackFilter = document.getElementById("graph-filter-track").value;
    const covFilter = document.getElementById("graph-filter-coverage").value;
    const q = (document.getElementById("graph-search-input").value || "").trim().toLowerCase();

    return (state.graphData.nodes || []).filter((n) => {
      if (sessFilter !== "all" && !n.sessions.includes(sessFilter)) return false;
      if (trackFilter !== "all" && n.track !== trackFilter) return false;
      if (covFilter !== "all" && n.coverage !== covFilter) return false;
      if (q) {
        const hay = (n.id + " " + n.name + " " + n.statement + " " + (n.aliases || []).join(" ")).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  function renderGraph() {
    const nodes = getFilteredGraphNodes();
    const visibleSet = new Set(nodes.map((n) => n.id));
    const chains = (state.graphData.chains || []).filter(
      (c) => visibleSet.has(c.conclusion) || c.premises.some((p) => visibleSet.has(p))
    );

    renderGraphCanvas(nodes, chains);
    renderChainTable(chains);
    if (state.selectedNodeId && state.graphData.nodesById && state.graphData.nodesById[state.selectedNodeId]) {
      openNodeDrawer(state.selectedNodeId);
    }
  }

  function renderGraphCanvas(nodes, chains) {
    const container = document.getElementById("graph-svg-container");
    if (!nodes.length) {
      container.innerHTML = '<div class="empty-state" style="margin:20px;">No proposition nodes match the active filter.</div>';
      return;
    }

    const nodeW = 200;
    const nodeH = 66;
    let edgesSvg = "";

    chains.forEach((ch) => {
      const toPos = state.graphPositions[ch.conclusion];
      if (!toPos) return;
      ch.premises.forEach((pid) => {
        const fromPos = state.graphPositions[pid];
        if (!fromPos) return;
        const x1 = fromPos.x + nodeW / 2;
        const y1 = fromPos.y + nodeH;
        const x2 = toPos.x + nodeW / 2;
        const y2 = toPos.y;
        const cy = (y1 + y2) / 2;
        const isCross = ch.crossSession;
        const stroke = isCross ? "#f59e0b" : "#64748b";
        const dash = isCross ? ' stroke-dasharray="6,4"' : "";
        const marker = isCross ? "url(#graph-arrow-cross)" : "url(#graph-arrow)";

        edgesSvg +=
          '<path d="M ' +
          x1 +
          " " +
          y1 +
          " C " +
          x1 +
          " " +
          cy +
          ", " +
          x2 +
          " " +
          cy +
          ", " +
          x2 +
          " " +
          (y2 - 5) +
          '" fill="none" stroke="' +
          stroke +
          '" stroke-width="' +
          (isCross ? "2.5" : "1.8") +
          '"' +
          dash +
          ' marker-end="' +
          marker +
          '" />';

        if (isCross) {
          const mx = (x1 + x2) / 2;
          const my = (y1 + y2) / 2;
          edgesSvg +=
            '<g transform="translate(' +
            (mx - 36) +
            "," +
            (my - 9) +
            ')">' +
            '<rect width="72" height="18" rx="4" fill="#1c1c1c" stroke="#f59e0b" stroke-width="1" />' +
            '<text x="36" y="12" text-anchor="middle" fill="#f59e0b" font-family="JetBrains Mono, monospace" font-size="9" font-weight="700">CROSS-LINK</text>' +
            "</g>";
        }
      });
    });

    let nodesSvg = "";
    nodes.forEach((n) => {
      const pos = state.graphPositions[n.id] || { x: 80, y: 60 };
      const covColor =
        n.coverage === "mastered" ? "#22c55e" : n.coverage === "shaky" ? "#f59e0b" : "#64748b";
      const isSelected = n.id === state.selectedNodeId;
      const shortName = n.name.length > 23 ? n.name.slice(0, 22) + "…" : n.name;
      const shortStmt = n.statement.length > 29 ? n.statement.slice(0, 28) + "…" : n.statement;

      nodesSvg +=
        '<g class="graph-node-g" data-node-id="' +
        esc(n.id) +
        '" transform="translate(' +
        pos.x +
        "," +
        pos.y +
        ')" style="cursor:pointer;">' +
        '<rect width="' +
        nodeW +
        '" height="' +
        nodeH +
        '" rx="8" fill="#1c1c1c" stroke="' +
        (isSelected ? "#38bdf8" : covColor) +
        '" stroke-width="' +
        (isSelected ? "3" : "2") +
        '" />' +
        '<circle cx="14" cy="18" r="5" fill="' +
        covColor +
        '" />' +
        '<text x="24" y="21" fill="#8d8d8d" font-family="JetBrains Mono, monospace" font-size="9.5">' +
        esc(n.id + " · d=" + n.depth + " · " + n.coverage) +
        "</text>" +
        '<text x="12" y="40" fill="#e6e6e6" font-family="Source Sans 3, sans-serif" font-size="13" font-weight="700">' +
        esc(shortName) +
        "</text>" +
        '<text x="12" y="56" fill="#8d8d8d" font-family="Source Sans 3, sans-serif" font-size="11">' +
        esc(shortStmt) +
        "</text>" +
        "<title>" +
        esc(n.name + "\n\n" + n.statement + "\n(Depth: " + n.depth + ", Coverage: " + n.coverage + ")") +
        "</title>" +
        "</g>";
    });

    const vp = state.graphViewport;
    container.innerHTML =
      '<svg id="graph-svg-el" width="100%" height="100%" viewBox="0 0 960 560">' +
      "<defs>" +
      '<marker id="graph-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">' +
      '<path d="M 0 1 L 10 5 L 0 9 z" fill="#64748b" />' +
      "</marker>" +
      '<marker id="graph-arrow-cross" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">' +
      '<path d="M 0 1 L 10 5 L 0 9 z" fill="#f59e0b" />' +
      "</marker>" +
      "</defs>" +
      '<g id="graph-svg-root" transform="translate(' +
      vp.x +
      "," +
      vp.y +
      ") scale(" +
      vp.k +
      ')">' +
      edgesSvg +
      nodesSvg +
      "</g>" +
      "</svg>";

    attachGraphInteractions(container);
  }

  function attachGraphInteractions(container) {
    attachSvgPanZoom(container, state.graphViewport, "graph-svg-root");

    // Node drag & click
    let draggingNodeId = null;
    let startMouse = { x: 0, y: 0 };
    let startNodePos = { x: 0, y: 0 };
    let moved = false;

    container.querySelectorAll(".graph-node-g").forEach((g) => {
      g.addEventListener("pointerdown", (e) => {
        e.stopPropagation();
        draggingNodeId = g.getAttribute("data-node-id");
        startMouse = { x: e.clientX, y: e.clientY };
        const cur = state.graphPositions[draggingNodeId] || { x: 0, y: 0 };
        startNodePos = { x: cur.x, y: cur.y };
        moved = false;
        g.setPointerCapture(e.pointerId);
      });

      g.addEventListener("pointermove", (e) => {
        if (!draggingNodeId) return;
        const dx = (e.clientX - startMouse.x) / (state.graphViewport.k || 1);
        const dy = (e.clientY - startMouse.y) / (state.graphViewport.k || 1);
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
        state.graphPositions[draggingNodeId] = {
          x: Math.round(startNodePos.x + dx),
          y: Math.round(startNodePos.y + dy),
        };
        g.setAttribute(
          "transform",
          "translate(" +
            state.graphPositions[draggingNodeId].x +
            "," +
            state.graphPositions[draggingNodeId].y +
            ")"
        );
      });

      g.addEventListener("pointerup", (e) => {
        if (!draggingNodeId) return;
        const nid = draggingNodeId;
        draggingNodeId = null;
        try {
          g.releasePointerCapture(e.pointerId);
        } catch (_) {}
        if (moved) {
          renderGraph();
          saveGraphPositions(true);
        } else {
          openNodeDrawer(nid);
        }
      });
    });
  }

  function openNodeDrawer(nodeId) {
    const byId = state.graphData.nodesById || {};
    const node = byId[nodeId];
    if (!node) return;
    state.selectedNodeId = nodeId;

    const ws = document.getElementById("graph-workspace");
    const drawer = document.getElementById("graph-drawer");
    ws.classList.remove("drawer-closed");
    drawer.style.display = "block";

    document.getElementById("drawer-node-id").textContent = node.id;
    const covEl = document.getElementById("drawer-node-coverage");
    covEl.textContent = node.coverage;
    covEl.className = "badge badge-" + node.coverage;
    document.getElementById("drawer-node-depth").textContent = "Depth " + node.depth;
    document.getElementById("drawer-node-name").textContent = node.name;
    document.getElementById("drawer-node-statement").textContent = node.statement;
    document.getElementById("drawer-node-aliases").textContent =
      node.aliases && node.aliases.length ? "Aliases: " + node.aliases.join(", ") : "";

    // Relations (premises & conclusions)
    const incoming = [];
    const outgoing = [];
    (state.graphData.chains || []).forEach((ch) => {
      if (ch.conclusion === node.id) {
        ch.premises.forEach((p) => incoming.push(p));
      }
      if (ch.premises.includes(node.id) && ch.conclusion) {
        outgoing.push(ch.conclusion);
      }
    });

    document.getElementById("drawer-node-relations").innerHTML =
      '<div class="field-box" style="margin-bottom:8px;">' +
      '<div class="field-label">Premises (' +
      incoming.length +
      ") &rarr; This Node &rarr; Conclusions (" +
      outgoing.length +
      ")</div>" +
      '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:4px;">' +
      (incoming.length
        ? incoming
            .map(
              (pid) =>
                '<button type="button" class="link-pill" data-select-node="' +
                esc(pid) +
                '">&larr; ' +
                esc((byId[pid] && byId[pid].name) || pid) +
                "</button>"
            )
            .join("")
        : '<span style="font-size:12px;color:var(--muted-foreground);">Root premise</span>') +
      (outgoing.length
        ? outgoing
            .map(
              (cid) =>
                '<button type="button" class="link-pill" data-select-node="' +
                esc(cid) +
                '">&rarr; ' +
                esc((byId[cid] && byId[cid].name) || cid) +
                "</button>"
            )
            .join("")
        : "") +
      "</div>" +
      "</div>";

    // Provenance records
    const provList = document.getElementById("drawer-provenance-list");
    const prov = node.provenance || [];
    if (prov.length === 0) {
      provList.innerHTML =
        '<div class="provenance-item">Session: ' +
        esc(node.sessions.join(", ") || "n/a") +
        "</div>";
    } else {
      provList.innerHTML = prov
        .map((p) => {
          const sid = p.session || p.chatId || p.sessionId || "";
          const step = p.step || "";
          const source = p.source || p.role || "extract";
          const snippet = p.snippet || p.quote || p.text || "";
          const ts = p.timestamp || p.at || "";
          return (
            '<div class="provenance-item">' +
            '<div style="display:flex;justify-content:space-between;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:4px;">' +
            '<div style="display:flex;gap:4px;flex-wrap:wrap;">' +
            (sid ? '<span class="badge">' + esc(sid) + "</span>" : "") +
            (step ? '<span class="badge">step: ' + esc(step) + "</span>" : "") +
            '<span class="badge badge-internship">' +
            esc(source) +
            "</span>" +
            "</div>" +
            (sid
              ? '<button type="button" class="btn btn-sm btn-brand" data-open-session="' +
                esc(sid) +
                '">Open Session</button>'
              : "") +
            "</div>" +
            (snippet ? '<div style="margin-top:4px;">' + esc(snippet) + "</div>" : "") +
            (ts
              ? '<div style="font-family:var(--font-mono);font-size:10px;color:var(--muted-foreground);margin-top:4px;">' +
                esc(fmtTime(ts)) +
                "</div>"
              : "") +
            "</div>"
          );
        })
        .join("");
    }
  }

  function renderChainTable(chains) {
    const byId = state.graphData.nodesById || {};
    const tbody = document.getElementById("graph-chain-tbody");
    if (!chains.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted-foreground);">No reasoning chains recorded yet.</td></tr>';
      return;
    }
    tbody.innerHTML = chains
      .map((ch) => {
        const premsHtml = (ch.premises || [])
          .map((pid) => {
            const n = byId[pid];
            return (
              '<button type="button" class="link-pill" data-select-node="' +
              esc(pid) +
              '">' +
              esc(pid + (n ? ": " + n.name : "")) +
              "</button>"
            );
          })
          .join(" ");
        const cn = byId[ch.conclusion];
        const conclHtml =
          '<button type="button" class="link-pill" data-select-node="' +
          esc(ch.conclusion) +
          '">' +
          esc(ch.conclusion + (cn ? ": " + cn.name : "")) +
          "</button>";
        const provText = (ch.provenance || [])
          .map((p) => (p.session || p.chatId || "") + (p.step ? " (" + p.step + ")" : ""))
          .filter(Boolean)
          .join(", ");
        return (
          "<tr>" +
          '<td><span class="badge">' +
          esc(ch.id) +
          "</span></td>" +
          "<td>" +
          (premsHtml || "(root)") +
          "</td>" +
          "<td>" +
          conclHtml +
          "</td>" +
          "<td>" +
          (ch.crossSession ? '<span class="badge badge-current">Cross-Session</span>' : '<span class="badge">Same Session</span>') +
          "</td>" +
          '<td style="font-family:var(--font-mono);font-size:11px;">' +
          esc(provText || "—") +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  async function saveGraphPositions(silent) {
    const statusEl = document.getElementById("graph-save-status");
    if (statusEl) statusEl.textContent = "Saving...";
    const body = JSON.stringify({ positions: state.graphPositions });
    const headers = { "Content-Type": "application/json" };
    try {
      let res = await fetch("/api/graph/positions", { method: "POST", headers, body });
      if (!res.ok) {
        res = await fetch("/api/graph", { method: "POST", headers, body });
      }
      if (statusEl) {
        statusEl.textContent = res.ok ? "Saved to positions.json ✓" : "Saved locally";
      }
    } catch (_) {
      if (!silent && statusEl) statusEl.textContent = "Saved in memory";
    }
  }

  // --- Shared SVG Pan/Zoom Helper ---
  function attachSvgPanZoom(container, vpState, rootGroupId) {
    let panning = false;
    let startPt = { x: 0, y: 0 };
    let startVp = { x: 0, y: 0 };

    container.onpointerdown = (e) => {
      if (e.target.closest(".graph-node-g")) return;
      panning = true;
      startPt = { x: e.clientX, y: e.clientY };
      startVp = { x: vpState.x, y: vpState.y };
      container.setPointerCapture(e.pointerId);
    };
    container.onpointermove = (e) => {
      if (!panning) return;
      vpState.x = Math.round(startVp.x + (e.clientX - startPt.x));
      vpState.y = Math.round(startVp.y + (e.clientY - startPt.y));
      const g = document.getElementById(rootGroupId);
      if (g) g.setAttribute("transform", "translate(" + vpState.x + "," + vpState.y + ") scale(" + vpState.k + ")");
    };
    container.onpointerup = (e) => {
      if (!panning) return;
      panning = false;
      try {
        container.releasePointerCapture(e.pointerId);
      } catch (_) {}
    };
    container.onwheel = (e) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.12 : 0.89;
      vpState.k = Math.max(0.25, Math.min(3.0, Number((vpState.k * factor).toFixed(2))));
      const g = document.getElementById(rootGroupId);
      if (g) g.setAttribute("transform", "translate(" + vpState.x + "," + vpState.y + ") scale(" + vpState.k + ")");
    };
  }

  // --- 5. PRACTICE VIEW (#practice) ---
  async function loadPractice(sessionId) {
    const sid = sessionId || "all";
    try {
      const raw = await apiGet("/api/practice/" + encodeURIComponent(sid));
      const attempts = Array.isArray(raw) ? raw : raw.attempts || raw.history || raw.events || [];
      const problems = raw.problems || raw.customProblems || [];
      state.practiceData = { attempts, problems };
      document.getElementById("badge-practice").textContent = String(attempts.length);
      renderPracticeView();
    } catch (err) {
      document.getElementById("practice-history-list").innerHTML =
        '<div class="empty-state">No practice attempts loaded (' + esc(err.message) + ").</div>";
    }
  }

  function renderPracticeView() {
    const resFilter = document.getElementById("practice-result-filter").value;
    const attempts = (state.practiceData.attempts || []).filter((a) => {
      const r = (a.result || a.score || "").toLowerCase();
      if (resFilter !== "all" && r !== resFilter) return false;
      return true;
    });

    const correctCount = attempts.filter((a) => (a.result || "").toLowerCase() === "correct").length;
    const partialCount = attempts.filter((a) => (a.result || "").toLowerCase() === "partial").length;
    const wrongCount = attempts.filter((a) => (a.result || "").toLowerCase() === "wrong").length;

    document.getElementById("practice-summary-badges").innerHTML =
      '<span class="badge badge-correct">Correct: ' +
      correctCount +
      "</span> " +
      '<span class="badge badge-partial">Partial: ' +
      partialCount +
      "</span> " +
      '<span class="badge badge-wrong">Wrong: ' +
      wrongCount +
      "</span>";

    const listEl = document.getElementById("practice-history-list");
    if (attempts.length === 0) {
      listEl.innerHTML = '<div class="empty-state">No practice attempts recorded for this filter.</div>';
    } else {
      listEl.innerHTML = attempts
        .map((a, idx) => {
          const res = (a.result || a.score || "partial").toLowerCase();
          const badgeClass = res === "correct" ? "badge-correct" : res === "wrong" ? "badge-wrong" : "badge-partial";
          const nodes = a.nodes || a.nodeIds || [];
          const diff =
            a.difficulty !== undefined
              ? typeof a.difficulty === "object"
                ? JSON.stringify(a.difficulty)
                : a.difficulty
              : (a.ideasCombined || 1) + " ideas / " + (a.cueFamiliarity || "standard");
          const hints = a.hints !== undefined ? a.hints : 0;

          return (
            '<div class="turn-card" data-kind="practice">' +
            '<div class="turn-header">' +
            '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">' +
            '<span class="badge">#' +
            (idx + 1) +
            "</span>" +
            (a.sessionId || a.session ? '<span class="badge">' + esc(a.sessionId || a.session) + "</span>" : "") +
            '<span class="badge ' +
            badgeClass +
            '">' +
            esc(res.toUpperCase()) +
            "</span>" +
            '<span class="badge">Difficulty: ' +
            esc(diff) +
            "</span>" +
            '<span class="badge">Hints used: ' +
            esc(hints) +
            "</span>" +
            nodes
              .map(
                (nid) =>
                  '<button type="button" class="link-pill" data-jump-graph-node="' +
                  esc(nid) +
                  '">' +
                  esc(nid) +
                  "</button>"
              )
              .join("") +
            "</div>" +
            '<div style="display:flex;gap:8px;align-items:center;">' +
            renderConfidenceRail(a.confidence) +
            '<span style="font-family:var(--font-mono);font-size:11px;color:var(--muted-foreground);">' +
            esc(fmtTime(a.timestamp || a.createdAt)) +
            "</span>" +
            "</div>" +
            "</div>" +
            '<div style="font-weight:600;margin-bottom:6px;">' +
            esc(a.problem || a.question || a.statement || "Practice Problem") +
            "</div>" +
            (a.thinking || a.think1
              ? '<div class="field-box"><div class="field-label">Learner Thinking Chunks</div><div class="field-value">' +
                esc(a.thinking || [a.think1, a.think2, a.think3].filter(Boolean).join("\n")) +
                "</div></div>"
              : "") +
            "</div>"
          );
        })
        .join("");
    }

    const probsEl = document.getElementById("practice-problems-list");
    const problems = state.practiceData.problems || [];
    if (problems.length === 0) {
      probsEl.innerHTML =
        '<div class="empty-state">Drop custom problem `.json` or `.md` files into `docs/learning/harness/data/problems/` to populate the custom bank.</div>';
    } else {
      probsEl.innerHTML = problems
        .map((p) => {
          const nodes = p.nodes || p.nodeIds || [];
          return (
            '<div class="session-card">' +
            "<div>" +
            '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px;">' +
            '<span class="badge badge-internship">' +
            esc(p.id || "problem") +
            "</span>" +
            '<span class="badge">Difficulty: ' +
            esc(p.difficulty || 1) +
            "</span>" +
            nodes.map((n) => '<span class="badge">' + esc(n) + "</span>").join("") +
            "</div>" +
            '<div style="font-weight:600;font-size:14px;">' +
            esc(p.problem || p.statement || p.title || "") +
            "</div>" +
            "</div>" +
            "</div>"
          );
        })
        .join("");
    }
  }

  // --- 6. JOBS & DEMOS VIEW (#jobs) ---
  async function loadJobs() {
    try {
      const raw = await apiGet("/api/jobs");
      const jobs = Array.isArray(raw) ? raw : raw.jobs || [];
      const demos = raw.demos || [];
      state.jobsData = { jobs, demos };
      const total = jobs.length + demos.length;
      document.getElementById("badge-jobs").textContent = String(total);
      document.getElementById("stat-total-jobs").textContent = String(total);
      renderJobsView();
    } catch (err) {
      document.getElementById("jobs-list-container").innerHTML =
        '<div class="empty-state">Could not load /api/jobs: ' + esc(err.message) + "</div>";
    }
  }

  function renderJobsView() {
    const kindFilter = document.getElementById("jobs-kind-filter").value;
    const statusFilter = document.getElementById("jobs-status-filter").value;

    const allItems = (state.jobsData.jobs || []).slice();
    // Merge standalone demos if not already listed as jobs
    (state.jobsData.demos || []).forEach((d) => {
      const did = d.id || d.name;
      if (!allItems.some((j) => j.id === did || j.demoId === did)) {
        allItems.push({
          id: did,
          kind: "demo",
          status: "done",
          worker: "inline",
          brief: d.title || d.description || "Interactive HTML Demo",
          output: d.path || d.file || "",
          demoId: did,
        });
      }
    });

    const filtered = allItems.filter((j) => {
      const k = (j.kind || "research").toLowerCase();
      const st = (j.status || "done").toLowerCase();
      if (kindFilter !== "all" && k !== kindFilter) return false;
      if (statusFilter !== "all") {
        if (statusFilter === "running" && st !== "running" && st !== "pending" && st !== "open") return false;
        if (statusFilter !== "running" && st !== statusFilter) return false;
      }
      return true;
    });

    const container = document.getElementById("jobs-list-container");
    if (filtered.length === 0) {
      container.innerHTML = '<div class="empty-state">No helper jobs or demos match the current filter.</div>';
      return;
    }

    container.innerHTML = filtered
      .map((j) => {
        const kind = (j.kind || "research").toLowerCase();
        const status = (j.status || "done").toLowerCase();
        const worker = j.worker || j.helper_worker || (kind === "demo" || kind === "research" ? "argon" : "inline");
        const statusBadge =
          status === "done" ? "badge-done" : status === "error" ? "badge-error" : "badge-running";
        const output = j.output || (j.result && (j.result.output || j.result.path)) || "";
        const demoId = j.demoId || (kind === "demo" ? j.id : "");
        const briefText =
          typeof j.brief === "string"
            ? j.brief
            : (j.result && j.result.brief) || j.title || j.description || "";

        return (
          '<div class="turn-card">' +
          '<div class="turn-header">' +
          '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">' +
          '<span class="badge badge-internship">' +
          esc(kind.toUpperCase()) +
          "</span>" +
          '<span class="badge ' +
          statusBadge +
          '">' +
          esc(status) +
          "</span>" +
          '<span class="badge">lane: ' +
          esc(worker) +
          "</span>" +
          '<span class="badge">' +
          esc(j.id) +
          "</span>" +
          "</div>" +
          '<span style="font-family:var(--font-mono);font-size:11px;color:var(--muted-foreground);">' +
          esc(fmtTime(j.updatedAt || j.createdAt)) +
          "</span>" +
          "</div>" +
          (briefText ? '<div style="margin-bottom:8px;font-size:13.5px;">' + esc(briefText) + "</div>" : "") +
          (output
            ? '<div style="font-family:var(--font-mono);font-size:11px;color:var(--muted-foreground);margin-bottom:8px;">Output: ' +
              esc(output) +
              "</div>"
            : "") +
          (j.argonCommand
            ? '<pre class="code-block" style="margin-bottom:8px;">' + esc(j.argonCommand) + "</pre>"
            : "") +
          (kind === "demo" || demoId
            ? '<button type="button" class="btn btn-sm btn-brand" data-open-demo="' +
              esc(demoId || j.id) +
              '">&#x25B6; Open Interactive Demo</button>'
            : "") +
          "</div>"
        );
      })
      .join("");
  }

  async function openDemoPreview(demoId) {
    if (!demoId) return;
    const iframe = document.getElementById("demo-iframe");
    const placeholder = document.getElementById("demo-empty-placeholder");
    const idBadge = document.getElementById("demo-preview-id");
    const reloadBtn = document.getElementById("btn-demo-reload");
    const newTabBtn = document.getElementById("btn-demo-newtab");

    try {
      const res = await fetch("/api/demo/" + encodeURIComponent(demoId));
      if (!res.ok) throw new Error("HTTP " + res.status);
      const ct = res.headers.get("content-type") || "";
      let html = "";
      let title = demoId;
      if (ct.includes("application/json")) {
        const data = await res.json();
        html = data.html || data.content || "";
        title = data.title || data.id || demoId;
      } else {
        html = await res.text();
      }
      state.activeDemoHtml = html;
      state.activeDemoTitle = title;

      placeholder.style.display = "none";
      iframe.style.display = "block";
      iframe.srcdoc = html;
      idBadge.style.display = "inline-flex";
      idBadge.textContent = title;
      reloadBtn.style.display = "inline-flex";
      newTabBtn.style.display = "inline-flex";
    } catch (err) {
      placeholder.style.display = "block";
      placeholder.textContent = "Could not load demo " + demoId + ": " + err.message;
      iframe.style.display = "none";
    }
  }

  // --- Action Modals (Continue / Link Session) ---
  function openContinueModal(sessionId) {
    const s = state.sessions.find((x) => x.id === sessionId) || { id: sessionId };
    const modal = document.getElementById("action-modal");
    document.getElementById("modal-title").textContent = "Continue Session — " + sessionId;
    document.getElementById("modal-body").innerHTML =
      '<p style="margin-top:0;font-size:13.5px;">Paste this slash command in Claude Code desktop to resume with full context:</p>' +
      '<pre class="code-block">/learn continue ' +
      esc(sessionId) +
      "</pre>" +
      '<p style="font-size:13px;color:var(--muted-foreground);">Or run the compact CLI resume pack directly:</p>' +
      '<pre class="code-block">python tools/learn/learn.py continue ' +
      esc(sessionId) +
      "</pre>" +
      '<div style="font-size:13px;margin-top:10px;">' +
      "<strong>Topic:</strong> " +
      esc(s.topic || s.title || "") +
      "<br/>" +
      "<strong>Goal:</strong> " +
      esc(s.goal || "—") +
      "<br/>" +
      "<strong>Progress:</strong> " +
      esc(s.doneCount || 0) +
      " / " +
      esc(s.stepCount || 0) +
      " steps" +
      "</div>";
    modal.classList.add("open");
  }

  function openLinkModal(sessionId) {
    const others = state.sessions.filter((x) => x.id !== sessionId);
    const otherId = others.length ? others[0].id : "<OTHER_SESSION_ID>";
    const modal = document.getElementById("action-modal");
    document.getElementById("modal-title").textContent = "Link Session — " + sessionId;
    document.getElementById("modal-body").innerHTML =
      '<p style="margin-top:0;font-size:13.5px;">Link <strong>' +
      esc(sessionId) +
      "</strong> with another session so cross-session reasoning chains and resume packs connect them:</p>" +
      '<pre class="code-block">python tools/learn/learn.py session link ' +
      esc(sessionId) +
      " " +
      esc(otherId) +
      "</pre>";
    modal.classList.add("open");
  }

  // --- Event Listeners & Boot ---
  function bindEvents() {
    document.querySelectorAll(".nav-tab").forEach((btn) => {
      btn.addEventListener("click", () => switchTab(btn.getAttribute("data-tab"), true));
    });

    document.getElementById("btn-theme-toggle").addEventListener("click", () => {
      applyTheme(state.theme === "dark" ? "light" : "dark");
    });

    document.getElementById("btn-refresh-all").addEventListener("click", () => {
      loadSessions();
      loadGraph();
      loadJobs();
      if (state.activeSessionId) {
        loadPlan(state.activeSessionId, true);
        if (state.activeTab === "session") loadSessionDetail(state.activeSessionId);
      }
    });

    document.getElementById("active-session-pill").addEventListener("click", () => {
      if (state.activeSessionId) switchTab("session", true);
    });

    // Sessions filters & search
    document.getElementById("filter-session-track").addEventListener("change", renderSessionsList);
    document.getElementById("filter-session-status").addEventListener("change", renderSessionsList);
    document.getElementById("btn-global-search").addEventListener("click", runGlobalSearch);
    document.getElementById("global-search-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") runGlobalSearch();
    });
    document.getElementById("btn-clear-search").addEventListener("click", () => {
      document.getElementById("global-search-input").value = "";
      runGlobalSearch();
    });
    document.getElementById("btn-close-search-panel").addEventListener("click", () => {
      document.getElementById("search-results-panel").style.display = "none";
    });

    // Session detail controls
    document.getElementById("session-picker-select").addEventListener("change", (e) => {
      loadSessionDetail(e.target.value);
    });
    document.getElementById("session-find-input").addEventListener("input", renderSessionDetail);
    document.getElementById("session-kind-filter").addEventListener("change", renderSessionDetail);
    document.getElementById("btn-session-to-plan").addEventListener("click", () => switchTab("plan", true));
    document.getElementById("btn-session-to-practice").addEventListener("click", () => switchTab("practice", true));
    document.getElementById("btn-session-continue").addEventListener("click", () => {
      if (state.activeSessionId) openContinueModal(state.activeSessionId);
    });
    document.getElementById("btn-session-link").addEventListener("click", () => {
      if (state.activeSessionId) openLinkModal(state.activeSessionId);
    });

    // Plan controls
    document.getElementById("plan-session-select").addEventListener("change", (e) => {
      loadPlan(e.target.value, true);
    });
    document.getElementById("btn-plan-refresh-now").addEventListener("click", () => {
      loadPlan(state.activeSessionId, true);
    });
    document.getElementById("btn-plan-zoom-in").addEventListener("click", () => {
      state.planViewport.k = Math.min(2.5, Number((state.planViewport.k * 1.2).toFixed(2)));
      if (state.planData) renderPlanSvgTree(state.planData.steps);
    });
    document.getElementById("btn-plan-zoom-out").addEventListener("click", () => {
      state.planViewport.k = Math.max(0.3, Number((state.planViewport.k * 0.85).toFixed(2)));
      if (state.planData) renderPlanSvgTree(state.planData.steps);
    });
    document.getElementById("btn-plan-zoom-fit").addEventListener("click", () => {
      state.planViewport = { x: 20, y: 20, k: 1 };
      if (state.planData) renderPlanSvgTree(state.planData.steps);
    });

    // Graph controls
    ["graph-filter-session", "graph-filter-track", "graph-filter-coverage"].forEach((id) => {
      document.getElementById(id).addEventListener("change", renderGraph);
    });
    document.getElementById("graph-search-input").addEventListener("input", renderGraph);
    document.getElementById("btn-graph-mode-canvas").addEventListener("click", () => {
      document.getElementById("btn-graph-mode-canvas").classList.add("active");
      document.getElementById("btn-graph-mode-table").classList.remove("active");
      document.getElementById("graph-workspace").style.display = "grid";
      document.getElementById("graph-chain-table-wrap").style.display = "none";
    });
    document.getElementById("btn-graph-mode-table").addEventListener("click", () => {
      document.getElementById("btn-graph-mode-table").classList.add("active");
      document.getElementById("btn-graph-mode-canvas").classList.remove("active");
      document.getElementById("graph-workspace").style.display = "none";
      document.getElementById("graph-chain-table-wrap").style.display = "block";
    });
    document.getElementById("btn-graph-zoom-in").addEventListener("click", () => {
      state.graphViewport.k = Math.min(3.0, Number((state.graphViewport.k * 1.2).toFixed(2)));
      renderGraph();
    });
    document.getElementById("btn-graph-zoom-out").addEventListener("click", () => {
      state.graphViewport.k = Math.max(0.25, Number((state.graphViewport.k * 0.85).toFixed(2)));
      renderGraph();
    });
    document.getElementById("btn-graph-zoom-fit").addEventListener("click", () => {
      state.graphViewport = { x: 40, y: 40, k: 1 };
      renderGraph();
    });
    document.getElementById("btn-graph-reset-layout").addEventListener("click", () => {
      const byDepth = {};
      (state.graphData.nodes || []).forEach((n) => {
        const d = n.depth || 0;
        if (!byDepth[d]) byDepth[d] = [];
        byDepth[d].push(n);
      });
      Object.keys(byDepth).forEach((dStr) => {
        const d = Number(dStr);
        byDepth[d].forEach((n, idx) => {
          state.graphPositions[n.id] = { x: 80 + idx * 240, y: 50 + d * 130 };
        });
      });
      renderGraph();
    });
    document.getElementById("btn-save-positions").addEventListener("click", () => saveGraphPositions(false));
    document.getElementById("btn-close-drawer").addEventListener("click", () => {
      state.selectedNodeId = null;
      document.getElementById("graph-drawer").style.display = "none";
      document.getElementById("graph-workspace").classList.add("drawer-closed");
      renderGraph();
    });

    // Practice controls
    document.getElementById("practice-session-select").addEventListener("change", (e) => {
      loadPractice(e.target.value);
    });
    document.getElementById("practice-result-filter").addEventListener("change", renderPracticeView);

    // Jobs & Demos controls
    document.getElementById("jobs-kind-filter").addEventListener("change", renderJobsView);
    document.getElementById("jobs-status-filter").addEventListener("change", renderJobsView);
    document.getElementById("btn-refresh-jobs").addEventListener("click", loadJobs);
    document.getElementById("btn-demo-reload").addEventListener("click", () => {
      const iframe = document.getElementById("demo-iframe");
      if (state.activeDemoHtml) iframe.srcdoc = state.activeDemoHtml;
    });
    document.getElementById("btn-demo-newtab").addEventListener("click", () => {
      if (!state.activeDemoHtml) return;
      const blob = new Blob([state.activeDemoHtml], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener");
    });

    // Modal close
    document.getElementById("btn-close-modal").addEventListener("click", () => {
      document.getElementById("action-modal").classList.remove("open");
    });
    document.getElementById("action-modal").addEventListener("click", (e) => {
      if (e.target.id === "action-modal") {
        document.getElementById("action-modal").classList.remove("open");
      }
    });

    // Global delegated clicks for data-* action attributes
    document.body.addEventListener("click", (e) => {
      const openSessBtn = e.target.closest("[data-open-session]");
      if (openSessBtn) {
        const sid = openSessBtn.getAttribute("data-open-session");
        state.activeSessionId = sid;
        switchTab("session", true);
        return;
      }

      const planSessBtn = e.target.closest("[data-plan-session]");
      if (planSessBtn) {
        state.activeSessionId = planSessBtn.getAttribute("data-plan-session");
        switchTab("plan", true);
        return;
      }

      const contSessBtn = e.target.closest("[data-continue-session]");
      if (contSessBtn) {
        openContinueModal(contSessBtn.getAttribute("data-continue-session"));
        return;
      }

      const linkSessBtn = e.target.closest("[data-link-session]");
      if (linkSessBtn) {
        openLinkModal(linkSessBtn.getAttribute("data-link-session"));
        return;
      }

      const hitEl = e.target.closest(".search-hit");
      if (hitEl) {
        const nid = hitEl.getAttribute("data-hit-node");
        const sid = hitEl.getAttribute("data-hit-session");
        if (nid) {
          state.selectedNodeId = nid;
          switchTab("graph", true);
        } else if (sid) {
          state.activeSessionId = sid;
          switchTab("session", true);
        }
        return;
      }

      const selNodeBtn = e.target.closest("[data-select-node]");
      if (selNodeBtn) {
        openNodeDrawer(selNodeBtn.getAttribute("data-select-node"));
        renderGraph();
        return;
      }

      const jumpGraphBtn = e.target.closest("[data-jump-graph-node]");
      if (jumpGraphBtn) {
        state.selectedNodeId = jumpGraphBtn.getAttribute("data-jump-graph-node");
        switchTab("graph", true);
        return;
      }

      const demoBtn = e.target.closest("[data-open-demo]");
      if (demoBtn) {
        openDemoPreview(demoBtn.getAttribute("data-open-demo"));
      }
    });
  }

  async function init() {
    applyTheme(state.theme);
    bindEvents();
    await Promise.all([loadSessions(), loadGraph(), loadJobs()]);
    startPlanAutoRefresh();

    // Parse initial hash
    const rawHash = (window.location.hash || "#sessions").slice(1);
    const parts = rawHash.split("/");
    if (parts[1]) state.activeSessionId = parts[1];
    switchTab(parts[0] || "sessions", false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
