"use strict";

const state = {
  drinker: "Me",
  currentAnalysis: null,
  rating: null,
};

const analysisForm = document.getElementById("analysisForm");
const drinkName = document.getElementById("drinkName");
const guestRow = document.getElementById("guestRow");
const guestName = document.getElementById("guestName");
const photoInput = document.getElementById("photoInput");
const appStatus = document.getElementById("appStatus");
const loader = document.getElementById("loader");

function showView(viewId) {
  document.querySelectorAll(".view").forEach((view) => {
    const active = view.id === viewId;
    view.hidden = !active;
    view.classList.toggle("active", active);
  });
  document.querySelectorAll(".nav").forEach((button) => {
    const active = button.dataset.view === viewId;
    button.classList.toggle("active", active);
    if (active) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });
  window.scrollTo({ top: 0, behavior: "instant" });
}

function setLoading(loading) {
  loader.hidden = !loading;
  loader.setAttribute("aria-hidden", String(!loading));
  analysisForm.querySelector("button[type='submit']").disabled = loading;
  photoInput.disabled = loading;
}

function errorMessage(payload, fallback) {
  if (typeof payload?.detail === "string") {
    return payload.detail;
  }
  if (Array.isArray(payload?.detail) && payload.detail.length > 0) {
    return payload.detail.map((item) => item.msg).join(" ");
  }
  return fallback;
}

function addTextElement(parent, tagName, className, text) {
  const element = document.createElement(tagName);
  if (className) {
    element.className = className;
  }
  element.textContent = text;
  parent.append(element);
  return element;
}

function renderAnalysis(analysis) {
  state.currentAnalysis = analysis;
  document.getElementById("resultTitle").textContent = analysis.identified_title;
  document.getElementById("resultOverview").textContent = analysis.overview;
  document.getElementById("resultContext").textContent = analysis.context;
  document.getElementById("resultTasting").textContent = analysis.tasting_notes;

  const meta = document.getElementById("resultMeta");
  meta.replaceChildren();
  [analysis.region, analysis.age_statement, analysis.abv, analysis.style]
    .filter(Boolean)
    .forEach((value) => addTextElement(meta, "span", "tag", value));

  const sourceList = document.getElementById("sourceList");
  sourceList.replaceChildren();
  analysis.sources.forEach((source) => {
    addTextElement(sourceList, "li", "", source);
  });
  document.getElementById("sourceSummary").textContent = analysis.sources.length
    ? `Research sources · ${analysis.sources.length}`
    : "Research sources · none in demo";

  state.rating = analysis.rating;
  document.getElementById("feedbackNotes").value = analysis.notes || "";
  document.getElementById("savedState").textContent = "";
  updateStars();
  showView("resultView");
}

async function submitAnalysis(photo = null) {
  appStatus.classList.remove("error");
  appStatus.textContent = "Preparing your briefing…";
  setLoading(true);

  const data = new FormData();
  data.append("drinker", state.drinker);
  const enteredName = drinkName.value.trim();
  if (enteredName) {
    data.append("drink_name", enteredName);
  }
  if (state.drinker === "Other" && guestName.value.trim()) {
    data.append("guest_name", guestName.value.trim());
  }
  if (photo) {
    data.append("photo", photo);
  }

  try {
    const response = await fetch("/api/analyses", { method: "POST", body: data });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(errorMessage(payload, "The glass could not be saved."));
    }
    appStatus.textContent = "Briefing ready.";
    renderAnalysis(payload);
  } catch (error) {
    appStatus.classList.add("error");
    appStatus.textContent = error.message || "The service is unavailable.";
  } finally {
    setLoading(false);
    photoInput.value = "";
  }
}

function updateStars() {
  document.querySelectorAll(".star").forEach((button) => {
    const selected = state.rating !== null && Number(button.dataset.rating) <= state.rating;
    button.classList.toggle("on", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function renderLastGlass(history) {
  const lastGlass = document.getElementById("lastGlass");
  lastGlass.replaceChildren();
  if (history.length === 0) {
    lastGlass.className = "mini-card empty";
    lastGlass.textContent = "No glasses saved yet.";
    return;
  }
  const latest = history[0];
  lastGlass.className = "mini-card";
  addTextElement(lastGlass, "span", "bottle-mark", "");
  const copy = addTextElement(lastGlass, "div", "mini-copy", "");
  addTextElement(copy, "strong", "", latest.identified_title);
  const drinker = latest.guest_name || latest.drinker;
  addTextElement(copy, "span", "", `${drinker} · ${latest.style}`);
  const score = latest.rating ? `${"★".repeat(latest.rating)}${"☆".repeat(5 - latest.rating)}` : "Not rated";
  addTextElement(lastGlass, "span", "score", score);
}

function historyDescription(analysis) {
  const who = analysis.guest_name || analysis.drinker;
  const score = analysis.rating ? `${analysis.rating}/5` : "Not rated yet";
  const detail = analysis.notes || analysis.style;
  return `${who} · ${score} · ${detail}`;
}

function renderHistory(history) {
  const historyList = document.getElementById("historyList");
  const historyStatus = document.getElementById("historyStatus");
  historyList.replaceChildren();
  if (history.length === 0) {
    historyStatus.textContent = "No recent glasses yet. Your next briefing will appear here.";
    renderLastGlass(history);
    return;
  }
  historyStatus.textContent = `${history.length} recent ${history.length === 1 ? "glass" : "glasses"}.`;
  history.forEach((analysis) => {
    const card = document.createElement("article");
    card.className = "history-card";
    addTextElement(card, "span", "bottle-mark", "");
    const copy = addTextElement(card, "div", "copy", "");
    addTextElement(copy, "h3", "", analysis.identified_title);
    addTextElement(copy, "p", "", historyDescription(analysis));
    historyList.append(card);
  });
  renderLastGlass(history);
}

async function loadHistory() {
  const historyStatus = document.getElementById("historyStatus");
  historyStatus.classList.remove("error");
  historyStatus.textContent = "Loading recent glasses…";
  try {
    const response = await fetch("/api/history", { headers: { Accept: "application/json" } });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(errorMessage(payload, "Recent glasses could not be loaded."));
    }
    renderHistory(payload);
  } catch (error) {
    historyStatus.classList.add("error");
    historyStatus.textContent = error.message || "Recent glasses are unavailable.";
  }
}

async function saveFeedback() {
  if (!state.currentAnalysis) {
    return;
  }
  const savedState = document.getElementById("savedState");
  const saveButton = document.getElementById("saveFeedback");
  const notes = document.getElementById("feedbackNotes").value.trim();
  const payload = { rating: state.rating, notes: notes || null };
  savedState.textContent = "Saving…";
  saveButton.disabled = true;
  try {
    const response = await fetch(
      `/api/analyses/${state.currentAnalysis.id}/feedback`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
    const result = await response.json();
    if (!response.ok) {
      throw new Error(errorMessage(result, "Feedback could not be saved."));
    }
    state.currentAnalysis.rating = result.rating;
    state.currentAnalysis.notes = result.notes;
    savedState.textContent = "Saved to your recent glasses.";
    await loadHistory();
  } catch (error) {
    savedState.textContent = error.message || "Feedback could not be saved.";
  } finally {
    saveButton.disabled = false;
  }
}

document.querySelectorAll(".star").forEach((button) => {
  button.addEventListener("click", () => {
    state.rating = Number(button.dataset.rating);
    updateStars();
  });
});

document.getElementById("saveFeedback").addEventListener("click", saveFeedback);

document.querySelectorAll("[data-drinker]").forEach((button) => {
  button.addEventListener("click", () => {
    state.drinker = button.dataset.drinker;
    document.querySelectorAll("[data-drinker]").forEach((choice) => {
      const selected = choice === button;
      choice.classList.toggle("active", selected);
      choice.setAttribute("aria-pressed", String(selected));
    });
    guestRow.hidden = state.drinker !== "Other";
    if (guestRow.hidden) {
      guestName.value = "";
    } else {
      guestName.focus();
    }
  });
});

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    showView(button.dataset.view);
    if (button.dataset.view === "historyView") {
      loadHistory();
    }
  });
});

analysisForm.addEventListener("submit", (event) => {
  event.preventDefault();
  submitAnalysis();
});

photoInput.addEventListener("change", () => {
  const photo = photoInput.files?.[0];
  if (photo) {
    submitAnalysis(photo);
  }
});

loadHistory();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/service-worker.js").catch(() => {
      appStatus.classList.add("error");
      appStatus.textContent = "Offline support could not be enabled.";
    });
  });
}
