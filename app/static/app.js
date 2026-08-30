"use strict";

const state = {
  drinker: "Me",
  currentAnalysis: null,
  rating: null,
  currentAudio: null,
  currentNarrationUrl: null,
};

const drinkerDisplayNames = {
  Me: "me",
  Wife: "Gin",
  Other: "guest",
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
  if (typeof payload?.detail?.message === "string") {
    return payload.detail.message;
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
  if (state.currentAudio) {
    state.currentAudio.pause();
    state.currentAudio = null;
  }
  state.currentAnalysis = analysis;
  state.currentNarrationUrl = analysis.narration_url || null;
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
    : "No grounded sources";

  state.rating = analysis.rating;
  document.getElementById("feedbackNotes").value = analysis.notes || "";
  document.getElementById("savedState").textContent = "";
  updateStars();
  const audioButton = document.getElementById("audioButton");
  audioButton.disabled = false;
  audioButton.setAttribute("aria-label", `Play narration for ${analysis.identified_title}`);
  document.getElementById("audioStatus").textContent = state.currentNarrationUrl
    ? "Saved narration ready to play."
    : "Play narration. Saved audio is reused when available.";
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

function displayDrinker(analysis) {
  return analysis.guest_name || drinkerDisplayNames[analysis.drinker] || analysis.drinker;
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
  const drinker = displayDrinker(latest);
  addTextElement(copy, "span", "", `${drinker} · ${latest.style}`);
  const score = latest.rating ? `${"★".repeat(latest.rating)}${"☆".repeat(5 - latest.rating)}` : "Not rated";
  addTextElement(lastGlass, "span", "score", score);
}

function historyDescription(analysis) {
  const who = displayDrinker(analysis);
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
    const card = document.createElement("button");
    card.type = "button";
    card.className = "history-card";
    card.setAttribute("aria-label", `Open review for ${analysis.identified_title}`);
    addTextElement(card, "span", "bottle-mark", "");
    const copy = addTextElement(card, "div", "copy", "");
    addTextElement(copy, "h3", "", analysis.identified_title);
    addTextElement(copy, "p", "", historyDescription(analysis));
    card.addEventListener("click", () => renderAnalysis(analysis));
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

async function settingsJsonFetch(url, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(url, { ...options, headers });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(errorMessage(payload, "The connection request failed."));
  }
  return payload;
}

function populateSettings(settings) {
  document.getElementById("modelDisplayName").value = settings.model.display_name;
  document.getElementById("modelBaseUrl").value = settings.model.base_url;
  document.getElementById("modelId").value = settings.model.model_id || "";
  document.getElementById("modelApiKey").value = "";
  document.getElementById("modelStatus").textContent = settings.model.api_key_configured
    ? "A write-only model key is configured."
    : "No model key is configured.";
  document.getElementById("voiceBaseUrl").value = settings.voice.base_url;
  document.getElementById("ttsModelId").value = settings.voice.tts_model_id;
  document.getElementById("outputFormat").value = settings.voice.output_format;
  document.getElementById("voiceApiKey").value = "";
  const voiceSelect = document.getElementById("voiceId");
  voiceSelect.replaceChildren();
  const option = document.createElement("option");
  option.value = settings.voice.voice_id || "";
  option.textContent = settings.voice.voice_name || "Refresh voices to choose";
  if (!settings.voice.voice_id) {
    option.dataset.placeholder = "true";
  }
  voiceSelect.append(option);
  document.getElementById("voiceStatus").textContent = settings.voice.api_key_configured
    ? "A write-only ElevenLabs key is configured."
    : "No ElevenLabs key is configured.";
}

async function loadConnectionSettings() {
  populateSettings(await settingsJsonFetch("/api/settings", { method: "GET" }));
}

function modelPayload(clearApiKey = false) {
  return {
    provider: "openai_compatible",
    display_name: document.getElementById("modelDisplayName").value.trim(),
    base_url: document.getElementById("modelBaseUrl").value.trim(),
    model_id: document.getElementById("modelId").value.trim() || null,
    api_key: clearApiKey
      ? null
      : document.getElementById("modelApiKey").value.trim() || null,
    clear_api_key: clearApiKey,
  };
}

function voicePayload(clearApiKey = false) {
  const voiceSelect = document.getElementById("voiceId");
  const placeholder = voiceSelect.selectedOptions[0]?.dataset.placeholder === "true";
  return {
    provider: "elevenlabs",
    base_url: document.getElementById("voiceBaseUrl").value,
    voice_id: placeholder ? null : voiceSelect.value || null,
    voice_name: placeholder ? null : voiceSelect.selectedOptions[0]?.textContent || null,
    tts_model_id: document.getElementById("ttsModelId").value.trim(),
    output_format: document.getElementById("outputFormat").value,
    api_key: clearApiKey
      ? null
      : document.getElementById("voiceApiKey").value.trim() || null,
    clear_api_key: clearApiKey,
  };
}

async function saveModelSettings(clearApiKey = false) {
  const result = await settingsJsonFetch("/api/settings/model", {
    method: "PUT",
    body: JSON.stringify(modelPayload(clearApiKey)),
  });
  document.getElementById("modelApiKey").value = "";
  document.getElementById("modelStatus").textContent = result.api_key_configured
    ? "Model settings saved; a key is configured."
    : "Model settings saved; no key is configured.";
}

async function saveVoiceSettings(clearApiKey = false) {
  const result = await settingsJsonFetch("/api/settings/voice", {
    method: "PUT",
    body: JSON.stringify(voicePayload(clearApiKey)),
  });
  document.getElementById("voiceApiKey").value = "";
  document.getElementById("voiceStatus").textContent = result.api_key_configured
    ? "Voice settings saved; a key is configured."
    : "Voice settings saved; no key is configured.";
}

async function modelAction(path) {
  const result = await settingsJsonFetch(path, {
    method: "POST",
    body: JSON.stringify({
      base_url: document.getElementById("modelBaseUrl").value.trim(),
      api_key: document.getElementById("modelApiKey").value.trim() || null,
    }),
  });
  if (result.models) {
    const options = document.getElementById("modelOptions");
    options.replaceChildren();
    result.models.forEach((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      options.append(option);
    });
    return `${result.models.length} models found.`;
  }
  return `Connection succeeded; ${result.models_count} models found.`;
}

async function voiceAction(path) {
  const result = await settingsJsonFetch(path, {
    method: "POST",
    body: JSON.stringify({
      base_url: document.getElementById("voiceBaseUrl").value,
      api_key: document.getElementById("voiceApiKey").value.trim() || null,
    }),
  });
  if (result.models) {
    const options = document.getElementById("ttsModelOptions");
    options.replaceChildren();
    result.models.forEach((model) => {
      const option = document.createElement("option");
      option.value = model.model_id;
      option.label = model.name || model.model_id;
      options.append(option);
    });
    return `${result.models.length} TTS models found.`;
  }
  if (result.voices) {
    const select = document.getElementById("voiceId");
    const selected = select.value;
    select.replaceChildren();
    result.voices.forEach((voice) => {
      const option = document.createElement("option");
      option.value = voice.voice_id;
      option.textContent = voice.name;
      option.selected = voice.voice_id === selected;
      select.append(option);
    });
    return `${result.voices.length} voices found.`;
  }
  return `Connection succeeded; ${result.voices_count} voices found.`;
}

async function generateNarration() {
  if (!state.currentAnalysis) {
    return;
  }
  const button = document.getElementById("audioButton");
  const statusElement = document.getElementById("audioStatus");
  button.disabled = true;
  statusElement.textContent = "Preparing narration…";
  try {
    let audioUrl = state.currentNarrationUrl;
    if (!audioUrl) {
      const response = await fetch(`/api/analyses/${state.currentAnalysis.id}/narration`, {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(errorMessage(payload, "Narration is unavailable."));
      }
      audioUrl = payload.audio_url;
      state.currentNarrationUrl = audioUrl;
      state.currentAnalysis.narration_url = audioUrl;
    }
    if (state.currentAudio) {
      state.currentAudio.pause();
    }
    state.currentAudio = new Audio(audioUrl);
    await state.currentAudio.play();
    statusElement.textContent = "Narration playing.";
  } catch (error) {
    statusElement.textContent = error.message || "Narration is unavailable.";
  } finally {
    button.disabled = false;
  }
}

document.querySelectorAll(".star").forEach((button) => {
  button.addEventListener("click", () => {
    state.rating = Number(button.dataset.rating);
    updateStars();
  });
});

document.getElementById("saveFeedback").addEventListener("click", saveFeedback);
document.getElementById("audioButton").addEventListener("click", generateNarration);

document.getElementById("modelSettingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await saveModelSettings();
  } catch (error) {
    document.getElementById("modelStatus").textContent = error.message;
  }
});

document.getElementById("voiceSettingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await saveVoiceSettings();
  } catch (error) {
    document.getElementById("voiceStatus").textContent = error.message;
  }
});

document.getElementById("clearModelKey").addEventListener("click", async () => {
  try {
    await saveModelSettings(true);
  } catch (error) {
    document.getElementById("modelStatus").textContent = error.message;
  }
});

document.getElementById("clearVoiceKey").addEventListener("click", async () => {
  try {
    await saveVoiceSettings(true);
  } catch (error) {
    document.getElementById("voiceStatus").textContent = error.message;
  }
});

document.getElementById("refreshModels").addEventListener("click", async () => {
  const statusElement = document.getElementById("modelStatus");
  try {
    statusElement.textContent = await modelAction("/api/settings/model/models");
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.getElementById("testModel").addEventListener("click", async () => {
  const statusElement = document.getElementById("modelStatus");
  try {
    statusElement.textContent = await modelAction("/api/settings/model/test");
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.getElementById("refreshVoices").addEventListener("click", async () => {
  const statusElement = document.getElementById("voiceStatus");
  try {
    statusElement.textContent = await voiceAction("/api/settings/voice/voices");
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.getElementById("refreshVoiceModels").addEventListener("click", async () => {
  const statusElement = document.getElementById("voiceStatus");
  try {
    statusElement.textContent = await voiceAction("/api/settings/voice/models");
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.getElementById("testVoice").addEventListener("click", async () => {
  const statusElement = document.getElementById("voiceStatus");
  try {
    statusElement.textContent = await voiceAction("/api/settings/voice/test");
  } catch (error) {
    statusElement.textContent = error.message;
  }
});

document.getElementById("previewVoice").addEventListener("click", async () => {
  const statusElement = document.getElementById("voiceStatus");
  let revokePreviewUrl = null;
  try {
    const response = await fetch("/api/settings/voice/preview", {
      method: "POST",
      headers: {
        Accept: "audio/mpeg",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        text: "Here’s a short preview from What’s in the Glass.",
        base_url: document.getElementById("voiceBaseUrl").value,
        api_key: document.getElementById("voiceApiKey").value.trim() || null,
        voice_id: document.getElementById("voiceId").value || null,
        tts_model_id: document.getElementById("ttsModelId").value.trim(),
        output_format: document.getElementById("outputFormat").value,
      }),
    });
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(errorMessage(payload, "Voice preview failed."));
    }
    const url = URL.createObjectURL(await response.blob());
    let revoked = false;
    revokePreviewUrl = () => {
      if (!revoked) {
        revoked = true;
        URL.revokeObjectURL(url);
      }
    };
    const preview = new Audio(url);
    preview.addEventListener("ended", revokePreviewUrl, { once: true });
    preview.addEventListener("error", revokePreviewUrl, { once: true });
    await preview.play();
    statusElement.textContent = "Voice preview playing.";
  } catch (error) {
    revokePreviewUrl?.();
    statusElement.textContent = error.message;
  }
});

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
    if (button.dataset.view === "connectionsView") {
      loadConnectionSettings();
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
