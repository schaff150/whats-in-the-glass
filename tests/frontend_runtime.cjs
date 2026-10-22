"use strict";

// No packages, browser, live server or network: only the shipped HTML/JS.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = { ...attrs };
    this.children = [];
    this.parentElement = null;
    this.listeners = new Map();
    this.dataset = {};
    for (const [name, value] of Object.entries(attrs)) {
      if (name.startsWith("data-")) {
        this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
      }
    }
    this.hidden = "hidden" in attrs;
    this.disabled = "disabled" in attrs;
    this.selected = "selected" in attrs;
    this._value = attrs.value;
    this.classList = {
      toggle: (name, force) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        const enabled = force === undefined ? !classes.has(name) : force;
        if (enabled) classes.add(name); else classes.delete(name);
        this.className = [...classes].join(" ");
        return enabled;
      },
      add: (name) => this.classList.toggle(name, true),
      remove: (name) => this.classList.toggle(name, false),
    };
  }
  get id() { return this.attributes.id || ""; }
  get type() { return this.attributes.type || ""; }
  set type(value) { this.attributes.type = value; }
  get className() { return this.attributes.class || ""; }
  set className(value) { this.attributes.class = value; }
  get textContent() {
    return this.children.map((child) => typeof child === "string" ? child : child.textContent).join("");
  }
  set textContent(value) { this.replaceChildren(String(value)); }
  get options() { return this.children.filter((child) => child.tagName === "OPTION"); }
  get selectedOptions() {
    const selected = this.options.filter((option) => option.selected);
    return selected.length ? selected : this.options.slice(0, 1);
  }
  get value() {
    if (this.tagName === "SELECT") return this.selectedOptions[0]?.value || "";
    if (this.tagName === "OPTION") return this._value ?? this.textContent;
    return this._value ?? "";
  }
  set value(value) {
    this._value = String(value);
    if (this.tagName === "SELECT") {
      this.options.forEach((option) => { option.selected = option.value === this._value; });
    }
  }
  append(...children) {
    for (const child of children) {
      if (typeof child !== "string") {
        child.parentElement = this;
        if (this.tagName === "SELECT" && child.selected) {
          this.options.forEach((option) => { option.selected = false; });
        }
      }
      this.children.push(child);
    }
  }
  replaceChildren(...children) {
    this.children.forEach((child) => { if (typeof child !== "string") child.parentElement = null; });
    this.children = [];
    this.append(...children);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, handler) {
    const handlers = this.listeners.get(type) || [];
    handlers.push(handler);
    this.listeners.set(type, handlers);
  }
  async emit(type) {
    const event = { target: this, preventDefault() {} };
    for (const handler of this.listeners.get(type) || []) await handler(event);
    await flush();
  }
  focus() {}
  matches(selector) {
    if (selector.startsWith("#")) return this.id === selector.slice(1);
    if (selector.startsWith(".")) return this.className.split(/\s+/).includes(selector.slice(1));
    const match = selector.match(/^([\w-]+)?\[([\w-]+)(?:=['"]?([^'"\]]+)['"]?)?\]$/);
    if (match) {
      return (!match[1] || this.tagName === match[1].toUpperCase()) &&
        match[2] in this.attributes && (match[3] === undefined || this.attributes[match[2]] === match[3]);
    }
    return this.tagName === selector.toUpperCase();
  }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => typeof child === "string" ? [] : [
      ...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector),
    ]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function build(node) {
  if (typeof node === "string") return node;
  const element = new Element(node.tag, node.attrs);
  element.append(...node.children.map(build));
  return element;
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const scenario = process.argv[3];
const document = build(JSON.parse(fs.readFileSync(0, "utf8")));
document.createElement = (tag) => new Element(tag);
document.getElementById = (id) => document.querySelector(`#${id}`);
const el = (id) => {
  const element = document.getElementById(id);
  assert.ok(element, `Missing shell control #${id}`);
  return element;
};

const settings = {
  model: { display_name: "Local model", base_url: "http://model.test/v1", model_id: "research", api_key_configured: true },
  voice: {
    provider: "voicebox", display_name: "Voicebox", base_url: "http://192.168.0.87:17600",
    voice_id: "hamish-local", voice_name: "Hamish", tts_model_id: "luxtts", output_format: "mp3",
    api_key_configured: true, // Even a legacy saved key must not imply Voicebox needs one.
  },
};
const requests = [];
const unexpectedRequests = [];
const routes = new Map([
  ["GET /api/history", () => []],
  ["GET /api/settings", () => settings],
]);
const audio = [];
const revoked = [];
let playError = null;
class HttpFailure {
  constructor(payload) { this.payload = payload; }
}
class FakeAudio extends Element {
  constructor(url) { super("audio"); this.url = url; audio.push(this); }
  async play() { if (playError) throw playError; }
  pause() {}
}
const context = vm.createContext({
  document, console, navigator: {}, window: { scrollTo() {}, addEventListener() {} },
  Audio: FakeAudio,
  URL: { createObjectURL: () => `blob:preview-${audio.length}`, revokeObjectURL: (url) => revoked.push(url) },
  fetch: async (url, options = {}) => {
    const method = options.method || "GET";
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ url, method, body, headers: options.headers || {} });
    const route = routes.get(`${method} ${url}`);
    if (!route) unexpectedRequests.push(`${method} ${url}`);
    assert.ok(route, `Unexpected offline request: ${method} ${url}`);
    const result = await route(body);
    return {
      ok: !(result instanceof HttpFailure),
      json: async () => result instanceof HttpFailure ? result.payload : result,
      blob: async () => Buffer.from("fixture audio"),
    };
  },
});

async function openConnections() {
  await document.querySelector('[data-view="connectionsView"]').emit("click");
  await flush();
}

function delayedVoiceWrites() {
  const writes = [];
  let apiKey = settings.voice.api_key_configured ? "saved-cloud-key" : null;
  routes.set("PUT /api/settings/voice", (body) => new Promise((resolve) => {
    writes.push({
      body,
      commit() {
        if (body.clear_api_key) apiKey = null;
        else if (body.api_key) apiKey = body.api_key;
        const { api_key, clear_api_key, ...selection } = body;
        Object.assign(settings.voice, selection, { api_key_configured: apiKey !== null });
        resolve({ ...settings.voice });
      },
      fail(message) { resolve(new HttpFailure({ detail: { message } })); },
    });
  }));
  return { writes, get apiKey() { return apiKey; } };
}

const scenarios = {
  async saved_voicebox() {
    await openConnections();
    assert.equal(el("voiceProvider").value, "voicebox");
    assert.deepEqual(el("voiceProvider").options.map((option) => option.value), ["elevenlabs", "voicebox"]);
    assert.equal(el("voiceBaseUrl").tagName, "INPUT", "Voicebox requires an editable endpoint");
    assert.equal(el("voiceBaseUrl").value, settings.voice.base_url);
    assert.match(el("voiceProviderTitle").textContent, /Voicebox/);
    assert.equal(el("voiceApiKey").type, "password");
    assert.equal(el("voiceApiKey").value, "");
    assert.equal(el("voiceApiKey").disabled, true);
    assert.equal(el("voiceKeyControls").hidden, true);
    assert.equal(el("clearVoiceKey").disabled, true);
    assert.equal(el("voiceId").value, "hamish-local");
    assert.equal(el("voiceId").selectedOptions[0].textContent, "Hamish");
    assert.equal(el("ttsModelId").value, "luxtts");
    assert.equal(el("outputFormat").value, "mp3");
    assert.match(el("voiceStatus").textContent, /Voicebox.*local/i);
    assert.match(el("voiceStatus").textContent, /no API key/i);
    assert.match(el("voiceStatus").textContent, /no ElevenLabs fallback/i);
    assert.equal(el("modelApiKey").value, "");
    assert.equal(el("modelId").value, "research");
  },
  async switch_provider() {
    // Old installations may not yet return a provider field: retain ElevenLabs.
    delete settings.voice.provider;
    Object.assign(settings.voice, {
      base_url: "https://api.elevenlabs.io", voice_id: "paid-voice", voice_name: "Cloud voice",
      tts_model_id: "eleven_multilingual_v2", output_format: "mp3_44100_128",
    });
    await openConnections();
    assert.equal(el("voiceProvider").value, "elevenlabs");
    assert.equal(el("voiceApiKey").disabled, false);
    assert.equal(el("voiceKeyControls").hidden, false);
    assert.match(el("voiceStatus").textContent, /ElevenLabs key is configured/);
    el("voiceApiKey").value = "unsaved-cloud-key";
    const staleModel = new Element("option", { value: "cloud-model" });
    el("ttsModelOptions").append(staleModel);
    el("voiceProvider").value = "voicebox";
    await el("voiceProvider").emit("change");
    assert.equal(el("voiceApiKey").value, "", "A provider switch must discard the cloud key");
    assert.equal(el("voiceApiKey").disabled, true);
    assert.equal(el("clearVoiceKey").disabled, true);
    assert.equal(el("voiceKeyControls").hidden, true);
    assert.equal(el("voiceBaseUrl").value, "http://192.168.0.87:17600");
    assert.equal(el("voiceId").value, "");
    assert.equal(el("voiceId").selectedOptions[0].dataset.placeholder, "true");
    assert.equal(el("ttsModelId").value, "luxtts");
    assert.equal(el("ttsModelOptions").children.length, 0);
    assert.equal(el("outputFormat").value, "mp3");
    assert.deepEqual(el("voiceBaseUrlOptions").options.map((option) => option.value), ["http://192.168.0.87:17600"]);
    assert.match(el("voiceStatus").textContent, /not saved/i);
    assert.match(el("voiceEndpointHelp").textContent, /no API key.*no ElevenLabs fallback/);

    const voice = new Element("option", { value: "other-hamish" });
    voice.textContent = "Other Hamish";
    el("voiceId").replaceChildren(voice);
    el("ttsModelOptions").append(new Element("option", { value: "other-model" }));
    el("voiceBaseUrl").value = "http://voicebox.test:17600";
    await el("voiceBaseUrl").emit("input");
    assert.equal(el("voiceId").value, "", "An edited endpoint must not keep another server's voice");
    assert.equal(el("ttsModelOptions").children.length, 0);

    el("voiceProvider").value = "elevenlabs";
    await el("voiceProvider").emit("change");
    assert.equal(el("voiceBaseUrl").value, "https://api.elevenlabs.io");
    assert.equal(el("voiceApiKey").value, "");
    assert.equal(el("voiceApiKey").disabled, false);
    assert.equal(el("voiceKeyControls").hidden, false);
    assert.equal(el("ttsModelId").value, "eleven_multilingual_v2");
    assert.equal(el("outputFormat").value, "mp3_44100_128");
    assert.equal(el("voiceId").value, "");
    assert.ok(el("voiceBaseUrlOptions").options.some((option) => option.value === "https://api.eu.residency.elevenlabs.io"));
    el("voiceApiKey").value = "discard-on-url-change";
    el("voiceBaseUrl").value = "https://api.us.elevenlabs.io";
    await el("voiceBaseUrl").emit("input");
    assert.equal(el("voiceApiKey").value, "", "An edited origin must discard the previous origin's unsaved key");
    el("voiceApiKey").value = "replacement-key";
    await el("voiceBaseUrl").emit("change");
    assert.equal(el("voiceApiKey").value, "replacement-key", "Do not erase a new key after the input event already reset the URL");
    assert.deepEqual(requests.map((request) => request.method), ["GET", "GET"], "Changing fields must not silently save");
  },
  async voicebox_actions() {
    await openConnections();
    el("voiceBaseUrl").value = "http://voicebox.test:17600";
    await el("voiceBaseUrl").emit("input");
    // Disabled fields are not a security boundary: payload construction must omit this.
    el("voiceApiKey").value = "stale-elevenlabs-key";
    const probe = { provider: "voicebox", base_url: "http://voicebox.test:17600", api_key: null };
    const hostileName = 'Hamish <img src=x onerror="alert(1)">';
    const responses = {
      voices: { voices: [{ voice_id: "draft-hamish", name: hostileName }] },
      models: { models: [{ model_id: "luxtts", name: "LuxTTS <script>bad()</script>" }] },
      test: { voices_count: 1 },
    };
    for (const [action, button] of [["voices", "refreshVoices"], ["models", "refreshVoiceModels"], ["test", "testVoice"]]) {
      routes.set(`POST /api/settings/voice/${action}`, (body) => {
        assert.deepEqual(body, probe);
        return responses[action];
      });
      await el(button).emit("click");
      assert.deepEqual(requests.at(-1).body, probe, `${action} must use the unsaved provider and keyless endpoint`);
      assert.match(el("voiceStatus").textContent, /found/);
      assert.match(el("voiceStatus").textContent, /Voicebox.*local.*no API key.*no ElevenLabs fallback/i);
    }
    assert.equal(el("voiceId").options[0].textContent, hostileName);
    assert.equal(el("voiceId").options[0].children.filter((child) => typeof child !== "string").length, 0);
    assert.equal(el("ttsModelOptions").options[0].label, "LuxTTS <script>bad()</script>");
    el("voiceId").value = "draft-hamish";
    el("ttsModelId").value = "luxtts-draft";
    const saved = {
      ...probe, voice_id: "draft-hamish", voice_name: hostileName,
      tts_model_id: "luxtts-draft", output_format: "mp3", clear_api_key: true,
    };
    routes.set("PUT /api/settings/voice", (body) => {
      assert.deepEqual(body, saved);
      Object.assign(settings.voice, body, { api_key_configured: false });
      return settings.voice;
    });
    await el("voiceSettingsForm").emit("submit");
    assert.deepEqual(requests.at(-1).body, saved, "Save must select Voicebox and explicitly clear legacy credentials");
    assert.equal(el("voiceApiKey").value, "");
    assert.match(el("voiceStatus").textContent, /saved/i);
    assert.match(el("voiceStatus").textContent, /Voicebox.*local.*no API key.*no ElevenLabs fallback/i);

    // Preview is an unsaved draft, not the saved selection.
    el("voiceBaseUrl").value = "http://draft-voicebox.test:17600";
    await el("voiceBaseUrl").emit("input");
    const draftVoice = new Element("option", { value: "preview-hamish" });
    draftVoice.textContent = "Preview Hamish";
    el("voiceId").replaceChildren(draftVoice);
    el("ttsModelId").value = "luxtts-preview";
    el("voiceApiKey").value = "another-stale-cloud-key";
    const preview = {
      text: "Here’s a short preview from What’s in the Glass.",
      provider: "voicebox", base_url: "http://draft-voicebox.test:17600", api_key: null,
      voice_id: "preview-hamish", tts_model_id: "luxtts-preview", output_format: "mp3",
    };
    routes.set("POST /api/settings/voice/preview", (body) => {
      assert.deepEqual(body, preview);
      return {};
    });
    await el("previewVoice").emit("click");
    assert.deepEqual(requests.at(-1).body, preview);
    assert.equal(requests.at(-1).headers.Accept, "audio/mpeg");
    assert.equal(audio.length, 1);
    assert.match(el("voiceStatus").textContent, /preview playing/i);
    assert.match(el("voiceStatus").textContent, /no ElevenLabs fallback/i);
    await audio[0].emit("ended");
    assert.deepEqual(revoked, [audio[0].url]);
    await audio[0].emit("error");
    assert.equal(revoked.length, 1, "Blob URLs are revoked only once");

    // The placeholder's label is never submitted as a voice ID or name.
    el("voiceBaseUrl").value = "http://empty-voicebox.test:17600";
    await el("voiceBaseUrl").emit("input");
    routes.set("PUT /api/settings/voice", (body) => {
      assert.equal(body.provider, "voicebox");
      assert.equal(body.voice_id, null);
      assert.equal(body.voice_name, null);
      assert.equal(body.api_key, null);
      return { ...body, api_key_configured: false };
    });
    await el("voiceSettingsForm").emit("submit");
    assert.equal(requests.at(-1).body.voice_id, null);
    assert.equal(requests.at(-1).body.voice_name, null);
  },
  async stale_responses() {
    await openConnections();
    const responses = {
      voices: { voices: [{ voice_id: "old-cloud-voice", name: "Old cloud voice" }] },
      models: { models: [{ model_id: "old-cloud-model", name: "Old cloud model" }] },
      test: { voices_count: 99 },
    };
    for (const [action, button] of [["voices", "refreshVoices"], ["models", "refreshVoiceModels"], ["test", "testVoice"]]) {
      el("voiceProvider").value = "elevenlabs";
      await el("voiceProvider").emit("change");
      let release;
      routes.set(`POST /api/settings/voice/${action}`, () => new Promise((resolve) => { release = resolve; }));
      const pending = el(button).emit("click");
      await flush();
      assert.ok(release, "The old provider request must really be pending");
      el("voiceProvider").value = "voicebox";
      await el("voiceProvider").emit("change");
      const currentStatus = el("voiceStatus").textContent;
      release(responses[action]);
      await pending;
      assert.equal(el("voiceId").value, "", "Old provider voices must not return after switching to Voicebox");
      assert.equal(el("ttsModelOptions").children.length, 0);
      assert.equal(el("voiceStatus").textContent, currentStatus, "An old provider result must not overwrite current status");
    }
    let release;
    routes.set("POST /api/settings/voice/voices", () => new Promise((resolve) => { release = resolve; }));
    const discovery = el("refreshVoices").emit("click");
    await flush();
    el("voiceBaseUrl").value = "http://another-voicebox.test:17600";
    await el("voiceBaseUrl").emit("input");
    release({ voices: [{ voice_id: "wrong-server-hamish", name: "Wrong Hamish" }] });
    await discovery;
    assert.equal(el("voiceId").value, "", "An old server response must not restore its voices after an endpoint edit");

    routes.set("PUT /api/settings/voice", () => new Promise((resolve) => { release = resolve; }));
    const save = el("voiceSettingsForm").emit("submit");
    await flush();
    el("voiceProvider").value = "elevenlabs";
    await el("voiceProvider").emit("change");
    el("voiceApiKey").value = "new-unsaved-key";
    const currentStatus = el("voiceStatus").textContent;
    release({ provider: "voicebox", api_key_configured: false });
    await save;
    assert.equal(el("voiceStatus").textContent, currentStatus, "A completed old save does not save the new selection");
    assert.equal(el("voiceApiKey").value, "new-unsaved-key");

    const cloudVoice = new Element("option", { value: "cloud-preview-voice" });
    cloudVoice.textContent = "Cloud preview voice";
    el("voiceId").replaceChildren(cloudVoice);
    release = null;
    routes.set("POST /api/settings/voice/preview", () => new Promise((resolve) => { release = resolve; }));
    const preview = el("previewVoice").emit("click");
    await flush();
    assert.ok(release, "The stale preview test must start a real request with a selected voice");
    el("voiceProvider").value = "voicebox";
    await el("voiceProvider").emit("change");
    const localStatus = el("voiceStatus").textContent;
    release({});
    await preview;
    assert.equal(audio.length, 0, "A stale cloud preview must not play after selecting local Voicebox");
    assert.equal(el("voiceStatus").textContent, localStatus);
  },
  async serialized_voice_saves() {
    Object.assign(settings.voice, {
      provider: "elevenlabs", base_url: "https://api.elevenlabs.io",
      voice_id: "cloud-hamish", voice_name: "Cloud Hamish",
      tts_model_id: "eleven_multilingual_v2", output_format: "mp3_44100_128",
    });
    await openConnections();
    const persisted = delayedVoiceWrites();
    const { writes } = persisted;
    el("voiceApiKey").value = "obsolete-cloud-key";
    const oldSave = el("voiceSettingsForm").emit("submit");
    await flush();
    assert.equal(writes.length, 1);
    assert.equal(writes[0].body.provider, "elevenlabs");

    el("voiceProvider").value = "voicebox";
    await el("voiceProvider").emit("change");
    const localVoice = new Element("option", { value: "draft-local-hamish" });
    localVoice.textContent = "Draft local Hamish";
    el("voiceId").replaceChildren(localVoice);
    el("ttsModelId").value = "luxtts-at-submit";
    const latestSave = el("voiceSettingsForm").emit("submit");
    await flush();
    // Later field edits must not change a queued save's captured payload.
    el("ttsModelId").value = "luxtts-edited-after-submit";
    const overlapped = writes.length > 1;
    if (overlapped) {
      // Reproduce the unsafe server order, not just a late response.
      writes[1].commit();
      await latestSave;
      assert.match(el("voiceStatus").textContent, /^Voice settings saved\..*Voicebox/);
    }
    writes[0].commit();
    await oldSave;
    if (!overlapped) {
      assert.equal(writes.length, 2, "The latest save starts after the old write is acknowledged");
      assert.doesNotMatch(el("voiceStatus").textContent, /^Voice settings saved\./);
      writes[1].commit();
      await latestSave;
    }

    assert.equal(settings.voice.provider, "voicebox", "An old ElevenLabs write must not restore the cancelled provider");
    assert.equal(settings.voice.voice_id, "draft-local-hamish");
    assert.equal(settings.voice.tts_model_id, "luxtts-at-submit");
    assert.equal(settings.voice.api_key_configured, false, "The final Voicebox write clears the persisted cloud key");
    assert.equal(persisted.apiKey, null);
    assert.equal(writes[1].body.api_key, null);
    assert.equal(writes[1].body.clear_api_key, true);
    assert.equal(overlapped, false, "A second write must not start before the first is acknowledged");
    assert.equal(el("voiceApiKey").value, "");
    assert.match(el("voiceStatus").textContent, /^Voice settings saved\..*Voicebox.*no API key.*no ElevenLabs fallback/i);
  },
  async voice_save_after_failure() {
    await openConnections();
    const persisted = delayedVoiceWrites();
    const { writes } = persisted;
    const failedSave = el("voiceSettingsForm").emit("submit");
    await flush();
    assert.equal(writes.length, 1);
    writes[0].fail("Voice settings could not be saved");
    await failedSave;
    assert.match(el("voiceStatus").textContent, /Voice settings could not be saved/);
    assert.equal(persisted.apiKey, "saved-cloud-key", "A rejected write does not mutate persisted credentials");

    el("voiceBaseUrl").value = "http://recovered-voicebox.test:17600";
    await el("voiceBaseUrl").emit("input");
    const voice = new Element("option", { value: "recovered-hamish" });
    voice.textContent = "Recovered Hamish";
    el("voiceId").replaceChildren(voice);
    const retry = el("voiceSettingsForm").emit("submit");
    await flush();
    assert.equal(writes.length, 2, "A failed save must not poison subsequent voice writes");
    writes[1].commit();
    await retry;
    assert.equal(settings.voice.provider, "voicebox");
    assert.equal(settings.voice.base_url, "http://recovered-voicebox.test:17600");
    assert.equal(settings.voice.voice_id, "recovered-hamish");
    assert.equal(settings.voice.api_key_configured, false);
    assert.equal(persisted.apiKey, null);
    assert.match(el("voiceStatus").textContent, /^Voice settings saved\..*Voicebox/);
  },
  async clear_key_write_ordering() {
    Object.assign(settings.voice, {
      provider: "elevenlabs", base_url: "https://api.elevenlabs.io",
      voice_id: "cloud-hamish", voice_name: "Cloud Hamish",
      tts_model_id: "eleven_multilingual_v2", output_format: "mp3_44100_128",
    });
    await openConnections();
    const persisted = delayedVoiceWrites();
    const { writes } = persisted;
    el("voiceApiKey").value = "first-key";
    const firstSave = el("voiceSettingsForm").emit("submit");
    await flush();
    el("voiceApiKey").value = "must-not-send-on-clear";
    const clear = el("clearVoiceKey").emit("click");
    await flush();
    el("voiceApiKey").value = "latest-key";
    const latestSave = el("voiceSettingsForm").emit("submit");
    await flush();
    assert.equal(writes.length, 1, "Save and clear-key actions must share one write queue");

    writes[0].commit();
    await firstSave;
    assert.equal(persisted.apiKey, "first-key");
    assert.equal(writes.length, 2);
    assert.equal(writes[1].body.api_key, null);
    assert.equal(writes[1].body.clear_api_key, true);
    assert.equal(el("voiceApiKey").value, "latest-key", "An older acknowledgement must not clear a queued save's key");
    assert.doesNotMatch(el("voiceStatus").textContent, /^Voice settings saved\./, "The latest save is still pending");

    writes[1].commit();
    await clear;
    assert.equal(persisted.apiKey, null, "The clear action really clears the persisted key in queue order");
    assert.equal(settings.voice.api_key_configured, false);
    assert.equal(writes.length, 3);
    assert.equal(writes[2].body.api_key, "latest-key", "A queued save retains the key captured at submit time");
    assert.equal(writes[2].body.clear_api_key, false);
    assert.equal(el("voiceApiKey").value, "latest-key");
    assert.doesNotMatch(el("voiceStatus").textContent, /^Voice settings saved\./);
    writes[2].commit();
    await latestSave;
    assert.equal(persisted.apiKey, "latest-key");
    assert.equal(settings.voice.provider, "elevenlabs");
    assert.equal(settings.voice.api_key_configured, true);
    assert.equal(el("voiceApiKey").value, "");
    assert.match(el("voiceStatus").textContent, /^Voice settings saved\..*ElevenLabs key is configured/);

    const finalClear = el("clearVoiceKey").emit("click");
    await flush();
    assert.equal(writes.length, 4);
    assert.doesNotMatch(el("voiceStatus").textContent, /^Voice settings saved\./, "A pending clear must not retain the previous saved status");
    writes[3].commit();
    await finalClear;
    assert.equal(persisted.apiKey, null);
    assert.equal(settings.voice.api_key_configured, false);
    assert.match(el("voiceStatus").textContent, /^Voice settings saved\..*No ElevenLabs key is configured/);
  },
  async preview_requires_current_voice() {
    await openConnections();
    assert.equal(el("voiceId").value, "hamish-local");
    el("voiceBaseUrl").value = "http://different-voicebox.test:17600";
    await el("voiceBaseUrl").emit("input");
    assert.equal(el("voiceId").value, "");
    assert.equal(el("voiceId").selectedOptions[0].dataset.placeholder, "true");
    assert.equal(settings.voice.voice_id, "hamish-local", "The API still has the old endpoint's saved voice");
    routes.set("POST /api/settings/voice/preview", () => ({}));
    const previousRequests = requests.length;
    await el("previewVoice").emit("click");
    assert.equal(requests.length, previousRequests, "An empty current voice must not request a preview that can fall back to the old saved profile");
    assert.equal(audio.length, 0, "The old endpoint's saved profile must not be synthesized or played");
    assert.deepEqual(revoked, []);
    assert.match(el("voiceStatus").textContent, /refresh voices.*select.*voice.*preview/i);
    assert.match(el("voiceStatus").textContent, /Voicebox.*no API key.*no ElevenLabs fallback/i);
  },
  async elevenlabs_actions() {
    Object.assign(settings.voice, {
      provider: "elevenlabs", display_name: "ElevenLabs", base_url: "https://api.eu.residency.elevenlabs.io",
      voice_id: "cloud-hamish", voice_name: "Cloud Hamish", tts_model_id: "eleven_multilingual_v2",
      output_format: "mp3_44100_128", api_key_configured: true,
    });
    await openConnections();
    assert.equal(el("voiceBaseUrl").value, settings.voice.base_url);
    assert.equal(el("outputFormat").value, "mp3_44100_128");
    assert.equal(el("voiceKeyControls").hidden, false);
    assert.equal(el("voiceApiKey").value, "");
    el("voiceApiKey").value = "replacement-cloud-key";
    const probe = { provider: "elevenlabs", base_url: settings.voice.base_url, api_key: "replacement-cloud-key" };
    const responses = {
      voices: { voices: [{ voice_id: "cloud-hamish", name: "Cloud Hamish" }] },
      models: { models: [{ model_id: "eleven_multilingual_v2", name: "Multilingual" }] },
      test: { voices_count: 1 },
    };
    for (const [action, button] of [["voices", "refreshVoices"], ["models", "refreshVoiceModels"], ["test", "testVoice"]]) {
      routes.set(`POST /api/settings/voice/${action}`, (body) => {
        assert.deepEqual(body, probe);
        return responses[action];
      });
      await el(button).emit("click");
      assert.deepEqual(requests.at(-1).body, probe);
      assert.match(el("voiceStatus").textContent, /found/);
    }
    const expectedSave = {
      ...probe, voice_id: "cloud-hamish", voice_name: "Cloud Hamish",
      tts_model_id: "eleven_multilingual_v2", output_format: "mp3_44100_128", clear_api_key: false,
    };
    routes.set("PUT /api/settings/voice", (body) => ({ ...body, api_key_configured: !body.clear_api_key }));
    await el("voiceSettingsForm").emit("submit");
    assert.deepEqual(requests.at(-1).body, expectedSave);
    assert.equal(el("voiceApiKey").value, "");
    assert.match(el("voiceStatus").textContent, /ElevenLabs key is configured/);
    el("voiceApiKey").value = "must-not-send-on-clear";
    await el("clearVoiceKey").emit("click");
    assert.deepEqual(requests.at(-1).body, { ...expectedSave, api_key: null, clear_api_key: true });
    assert.equal(el("voiceApiKey").value, "");
    assert.match(el("voiceStatus").textContent, /No ElevenLabs key is configured/);

    el("voiceApiKey").value = "preview-cloud-key";
    el("ttsModelId").value = "eleven_draft_model";
    routes.set("POST /api/settings/voice/preview", () => ({}));
    playError = new Error("Playback denied by browser");
    await el("previewVoice").emit("click");
    assert.deepEqual(requests.at(-1).body, {
      text: "Here’s a short preview from What’s in the Glass.", provider: "elevenlabs",
      base_url: settings.voice.base_url, api_key: "preview-cloud-key", voice_id: "cloud-hamish",
      tts_model_id: "eleven_draft_model", output_format: "mp3_44100_128",
    });
    assert.equal(audio.length, 1);
    assert.deepEqual(revoked, [audio[0].url], "A play rejection must release the preview blob");
    assert.match(el("voiceStatus").textContent, /Playback denied by browser/);
  },
  async voicebox_errors() {
    await openConnections();
    const message = 'Local Voicebox unavailable <img src=x onerror="alert(1)">';
    for (const [action, button] of [["voices", "refreshVoices"], ["models", "refreshVoiceModels"], ["test", "testVoice"], ["preview", "previewVoice"]]) {
      routes.set(`POST /api/settings/voice/${action}`, () => new HttpFailure({ detail: { message } }));
      const previousRequests = requests.length;
      await el(button).emit("click");
      assert.equal(requests.length, previousRequests + 1, "A failed local request must not retry against ElevenLabs");
      assert.equal(requests.at(-1).body.provider, "voicebox");
      assert.equal(requests.at(-1).body.api_key, null);
      assert.ok(el("voiceStatus").textContent.includes(message));
      assert.match(el("voiceStatus").textContent, /no API key.*no ElevenLabs fallback/i);
      assert.equal(el("voiceStatus").children.filter((child) => typeof child !== "string").length, 0);
    }
    routes.set("PUT /api/settings/voice", () => new HttpFailure({ detail: { message } }));
    await el("voiceSettingsForm").emit("submit");
    assert.ok(el("voiceStatus").textContent.includes(message));
    assert.match(el("voiceStatus").textContent, /no API key.*no ElevenLabs fallback/i);
    assert.equal(audio.length, 0);
    assert.deepEqual(revoked, []);
  },
};

(async () => {
  assert.ok(scenarios[scenario], `Unknown scenario ${scenario}`);
  vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context, { filename: "app/static/app.js" });
  await flush();
  await scenarios[scenario]();
  assert.deepEqual(unexpectedRequests, [], "Every request must have an offline fixture");
  console.log(`PASS ${scenario}`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
