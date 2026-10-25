/* Prescription Sahayak — client */
(() => {
  "use strict";

  const MAX_UPLOAD_BYTES = 4 * 1024 * 1024; // matches the server / Vercel body limit
  const MAX_IMAGE_SIDE = 2000;
  const SPEAK_CHUNK_CHARS = 1500; // per /api/speak request (server max 2000)
  const PREFS_KEY = "sahayak:prefs";
  const STEPS = ["digitise", "analyse", "translate", "speak"];

  const $ = (id) => document.getElementById(id);
  const els = {
    form: $("rx-form"),
    dropzone: $("dropzone"),
    dzEmpty: $("dz-empty"),
    dzPreview: $("dz-preview"),
    previewImg: $("preview-img"),
    previewPdf: $("preview-pdf"),
    fileName: $("file-name"),
    fileError: $("file-error"),
    cameraInput: $("camera-input"),
    fileInput: $("file-input"),
    changeFile: $("change-file"),
    language: $("language"),
    voice: $("voice"),
    voiceHint: $("voice-hint"),
    sourceLanguage: $("source-language"),
    submit: $("submit"),
    progress: $("progress"),
    progressLive: $("progress-live"),
    error: $("error"),
    errorMessage: $("error-message"),
    retry: $("retry"),
    results: $("results"),
    resultLanguage: $("result-language"),
    play: $("play"),
    seek: $("seek"),
    audio: $("audio"),
    timeCurrent: $("time-current"),
    timeTotal: $("time-total"),
    listenStatus: $("listen-status"),
    summary: $("summary"),
    downloadAudio: $("download-audio"),
    copySummary: $("copy-summary"),
    share: $("share"),
    meta: $("meta"),
    uncertain: $("uncertain"),
    medicines: $("medicines"),
    medCount: $("med-count"),
    extras: $("extras"),
    ocrText: $("ocr-text"),
    startOver: $("start-over"),
  };

  const state = {
    languages: [],
    upload: null, // { blob, name, type }
    previewUrl: null,
    ocrText: null,
    prescription: null,
    summaryEn: null,
    translations: new Map(), // lang -> text
    audio: new Map(), // `${lang}|${voice}|${pace}` -> Blob
    audioUrl: null,
    current: null, // { lang, text, blob }
    busy: false,
    lastAction: null,
  };

  // ---------------------------------------------------------------- utilities
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  const formatTime = (s) => {
    if (!Number.isFinite(s) || s < 0) return "0:00";
    const m = Math.floor(s / 60);
    return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  };

  const loadPrefs = () => {
    try { return JSON.parse(localStorage.getItem(PREFS_KEY)) || {}; } catch { return {}; }
  };
  const savePrefs = () => {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify({ language: els.language.value, voice: els.voice.value, pace: getPace() }));
    } catch { /* storage unavailable (private mode) */ }
  };

  const getPace = () => Number(els.form.querySelector('input[name="pace"]:checked')?.value || 1);
  const languageByCode = (code) => state.languages.find((l) => l.code === code);

  // ---------------------------------------------------------------- API
  async function api(path, options = {}) {
    let response;
    try {
      response = await fetch(path, options);
    } catch {
      throw new Error("We couldn't reach the server. Please check your internet connection and try again.");
    }
    if (!response.ok) {
      let message = `Something went wrong (error ${response.status}). Please try again.`;
      if (response.status === 413) message = "That file is too large. Please use a smaller photo or PDF.";
      if (response.status === 504) message = "The server took too long to respond. Please try again.";
      try {
        const body = await response.json();
        if (typeof body.detail === "string") message = body.detail;
      } catch { /* non-JSON error body */ }
      throw new Error(message);
    }
    return response;
  }

  /** Split text on line boundaries into pieces no longer than `limit` characters. */
  function chunkLines(text, limit) {
    const chunks = [];
    let current = "";
    const lines = text.split(/\n+/).map((l) => l.trim()).filter(Boolean);
    for (const line of lines) {
      // Very long lines are cut at the last space before the limit.
      const pieces = [];
      let rest = line;
      while (rest.length > limit) {
        const cut = rest.lastIndexOf(" ", limit);
        const at = cut > 0 ? cut : limit;
        pieces.push(rest.slice(0, at).trim());
        rest = rest.slice(at).trim();
      }
      if (rest) pieces.push(rest);

      for (const piece of pieces) {
        const candidate = current ? `${current}\n${piece}` : piece;
        if (candidate.length <= limit) current = candidate;
        else { if (current) chunks.push(current); current = piece; }
      }
    }
    if (current) chunks.push(current);
    return chunks;
  }

  const postJSON = (path, body) =>
    api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  // ---------------------------------------------------------------- languages
  function fillLanguageSelect(select, selected) {
    select.replaceChildren(...state.languages.map((l) => el("option", { value: l.code, text: l.label })));
    if (selected && languageByCode(selected)) select.value = selected;
  }

  function fillVoices(preferred) {
    const lang = languageByCode(els.language.value);
    if (!lang) return;
    els.voice.replaceChildren(...lang.voices.map((v) => el("option", { value: v.id, text: v.label })));
    if (preferred && lang.voices.some((v) => v.id === preferred)) els.voice.value = preferred;
    const fallback = lang.tts_model !== "bulbul:v4-flash";
    els.voiceHint.hidden = !fallback;
    els.voiceHint.textContent = fallback ? `${lang.name} uses Bulbul v3 voices.` : "";
  }

  async function loadLanguages() {
    try {
      state.languages = await (await api("/api/languages")).json();
    } catch (err) {
      showError(err.message, loadLanguages);
      return;
    }
    const prefs = loadPrefs();
    fillLanguageSelect(els.language, prefs.language || "hi-IN");
    fillLanguageSelect(els.resultLanguage, els.language.value);
    fillLanguageSelect(els.sourceLanguage, "en-IN");
    fillVoices(prefs.voice);
    if (prefs.pace) {
      const radio = els.form.querySelector(`input[name="pace"][value="${prefs.pace}"]`);
      if (radio) radio.checked = true;
    }
    [els.language, els.voice, els.sourceLanguage].forEach((s) => (s.disabled = false));
    hideError();
    updateSubmit();
  }

  // ---------------------------------------------------------------- file input
  function loadImage(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("This image couldn't be opened.")); };
      img.src = url;
    });
  }

  /** Downscale and re-encode photos so uploads are fast and stay under the size limit. */
  async function prepareImage(file) {
    let source;
    try {
      source = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      source = await loadImage(file);
    }
    const scale = Math.min(1, MAX_IMAGE_SIDE / Math.max(source.width, source.height));
    const width = Math.round(source.width * scale);
    const height = Math.round(source.height * scale);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(source, 0, 0, width, height);
    if (source.close) source.close();

    let quality = 0.9;
    let blob;
    do {
      blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
      quality -= 0.15;
    } while (blob && blob.size > MAX_UPLOAD_BYTES && quality > 0.4);
    if (!blob || blob.size > MAX_UPLOAD_BYTES) throw new Error("This image is too large to process. Please try another photo.");
    return blob;
  }

  async function handleFile(file) {
    if (!file) return;
    els.fileError.hidden = true;
    const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
    const isImage = file.type.startsWith("image/");
    if (!isPdf && !isImage) return fileError("Please choose a photo (JPG, PNG) or a PDF.");
    if (isPdf && file.size > MAX_UPLOAD_BYTES) return fileError("This PDF is larger than 4 MB. Please upload a photo of the prescription instead.");

    resetAnalysis();
    try {
      const blob = isPdf ? file : await prepareImage(file);
      state.upload = { blob, name: isPdf ? file.name : "prescription.jpg", type: isPdf ? "application/pdf" : "image/jpeg" };
    } catch (err) {
      return fileError(err.message);
    }

    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = isPdf ? null : URL.createObjectURL(state.upload.blob);
    els.previewImg.hidden = isPdf;
    els.previewPdf.hidden = !isPdf;
    if (!isPdf) els.previewImg.src = state.previewUrl;
    els.fileName.textContent = file.name || (isPdf ? "Prescription.pdf" : "Photo");
    els.dzEmpty.hidden = true;
    els.dzPreview.hidden = false;
    els.dropzone.classList.add("has-file");
    updateSubmit();
  }

  function fileError(message) {
    els.fileError.textContent = message;
    els.fileError.hidden = false;
  }

  function clearFile() {
    state.upload = null;
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = null;
    els.previewImg.removeAttribute("src");
    els.cameraInput.value = "";
    els.fileInput.value = "";
    els.dzEmpty.hidden = false;
    els.dzPreview.hidden = true;
    els.dropzone.classList.remove("has-file");
    resetAnalysis();
    updateSubmit();
  }

  function resetAnalysis() {
    state.ocrText = null;
    state.prescription = null;
    state.summaryEn = null;
    state.translations.clear();
    state.audio.clear();
    state.current = null;
    stopAudio();
    els.results.hidden = true;
    els.progress.hidden = true;
    hideError();
  }

  // ---------------------------------------------------------------- UI state
  function updateSubmit() {
    els.submit.disabled = state.busy || !state.upload || !state.languages.length;
  }

  function setBusy(busy, label) {
    state.busy = busy;
    const btnLabel = els.submit.querySelector(".btn-label");
    els.submit.querySelector(".spinner")?.remove();
    if (busy) {
      els.submit.prepend(el("span", { class: "spinner", "aria-hidden": "true" }));
      btnLabel.textContent = label || "Working…";
    } else {
      btnLabel.textContent = "Read my prescription";
    }
    els.resultLanguage.disabled = busy;
    updateSubmit();
  }

  function setStep(name, status) {
    const li = els.progress.querySelector(`[data-step="${name}"]`);
    if (!li) return;
    li.classList.remove("is-active", "is-done", "is-error", "is-skipped");
    if (status) li.classList.add(`is-${status}`);
    if (status === "active") {
      li.setAttribute("aria-current", "step");
      els.progressLive.textContent = li.querySelector(".label").textContent;
    } else {
      li.removeAttribute("aria-current");
    }
  }

  function showError(message, retry) {
    els.errorMessage.textContent = message;
    els.error.hidden = false;
    state.lastAction = retry || null;
    els.retry.hidden = !retry;
    els.error.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function hideError() {
    els.error.hidden = true;
  }

  function setListenStatus(text) {
    els.listenStatus.hidden = !text;
    els.listenStatus.lastElementChild.textContent = text || "";
    els.summary.classList.toggle("is-stale", Boolean(text));
    els.play.disabled = Boolean(text);
  }

  // ---------------------------------------------------------------- pipeline
  async function runFull() {
    if (!state.upload || state.busy) return;
    hideError();
    setBusy(true, "Reading your prescription…");
    els.results.hidden = true;
    els.progress.hidden = false;
    STEPS.forEach((s) => setStep(s, null));
    els.progress.scrollIntoView({ behavior: "smooth", block: "center" });

    const lang = els.language.value;
    let step = "digitise";
    try {
      if (!state.ocrText) {
        setStep("digitise", "active");
        const form = new FormData();
        form.append("file", state.upload.blob, state.upload.name);
        form.append("language", els.sourceLanguage.value);
        state.ocrText = (await (await api("/api/digitise", { method: "POST", body: form })).json()).text;
      }
      setStep("digitise", "done");

      step = "analyse";
      if (!state.prescription) {
        setStep("analyse", "active");
        const data = await (await postJSON("/api/analyse", { ocr_text: state.ocrText })).json();
        state.prescription = data.prescription;
        state.summaryEn = data.summary;
      }
      setStep("analyse", "done");
      renderPrescription();

      step = "translate";
      await produceLanguage(lang, {
        onTranslate: () => setStep("translate", "active"),
        onSpeak: () => { setStep("translate", "done"); setStep("speak", "active"); },
      });
      setStep("translate", "done");
      setStep("speak", "done");

      els.progress.hidden = true;
      els.results.hidden = false;
      els.results.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      const failed = STEPS.find((s) => els.progress.querySelector(`[data-step="${s}"]`).classList.contains("is-active")) || step;
      setStep(failed, "error");
      showError(err.message, runFull);
    } finally {
      setBusy(false);
    }
  }

  /** Translate + synthesise for one language, using cached results when available. */
  async function produceLanguage(lang, hooks = {}) {
    const language = languageByCode(lang);
    const voice = lang === els.language.value ? els.voice.value : language.voices[0].id;
    const pace = getPace();

    // Keyed by voice too: the translation matches the speaker's gender in first-person phrases.
    const textKey = `${lang}|${voice}`;
    let text = state.translations.get(textKey);
    if (!text) {
      hooks.onTranslate?.();
      text = (await (await postJSON("/api/translate", { text: state.summaryEn, target: lang, voice })).json()).text;
      state.translations.set(textKey, text);
    }

    const key = `${lang}|${voice}|${pace}`;
    let blob = state.audio.get(key);
    if (!blob) {
      hooks.onSpeak?.();
      const parts = [];
      for (const chunk of chunkLines(text, SPEAK_CHUNK_CHARS)) {
        parts.push(await (await postJSON("/api/speak", { text: chunk, language: lang, voice, pace })).blob());
      }
      // MP3 is frame-based, so the parts can be joined into one playable file.
      blob = new Blob(parts, { type: "audio/mpeg" });
      state.audio.set(key, blob);
    }

    state.current = { lang, text, blob };
    renderListen();
  }

  async function switchLanguage(lang) {
    if (!state.summaryEn || state.busy) return;
    els.language.value = lang;
    fillVoices();
    savePrefs();
    hideError();
    const name = languageByCode(lang)?.name || "";
    state.busy = true;
    els.resultLanguage.disabled = true;
    updateSubmit();
    try {
      await produceLanguage(lang, {
        onTranslate: () => setListenStatus(`Translating into ${name}…`),
        onSpeak: () => setListenStatus(`Preparing ${name} audio…`),
      });
    } catch (err) {
      if (state.current) els.resultLanguage.value = state.current.lang;
      showError(err.message, () => switchLanguage(lang));
    } finally {
      setListenStatus(null);
      state.busy = false;
      els.resultLanguage.disabled = false;
      updateSubmit();
    }
  }

  // ---------------------------------------------------------------- rendering
  function renderListen() {
    const { lang, text, blob } = state.current;
    els.resultLanguage.value = lang;
    els.summary.lang = lang.split("-")[0];
    els.summary.replaceChildren(...text.split(/\n+/).filter(Boolean).map((line) => el("p", { text: line })));

    stopAudio();
    if (state.audioUrl) URL.revokeObjectURL(state.audioUrl);
    state.audioUrl = URL.createObjectURL(blob);
    els.audio.src = state.audioUrl;
    els.downloadAudio.href = state.audioUrl;
    els.downloadAudio.download = `prescription-${lang}.mp3`;
  }

  function renderPrescription() {
    const rx = state.prescription;
    els.ocrText.textContent = state.ocrText;

    const metaItems = [
      ["Patient", [rx.patient_name, rx.patient_age && `${rx.patient_age} yrs`].filter(Boolean).join(" · ")],
      ["Doctor", rx.doctor_name],
      ["Clinic", rx.clinic],
      ["Date", rx.date],
    ].filter(([, v]) => v);
    els.meta.hidden = !metaItems.length;
    els.meta.replaceChildren(...metaItems.map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v }))));

    els.uncertain.hidden = !rx.uncertain.length;
    els.uncertain.querySelector("ul").replaceChildren(...rx.uncertain.map((u) => el("li", { text: u })));

    els.medCount.textContent = rx.medicines.length || "";
    els.medCount.hidden = !rx.medicines.length;
    if (rx.medicines.length) {
      els.medicines.replaceChildren(...rx.medicines.map(renderMedicine));
    } else {
      els.medicines.replaceChildren(
        el("p", { class: "empty-meds", text: "We couldn't find any medicines in this image. If this is a prescription, try a sharper, well-lit photo." })
      );
    }

    const sections = [];
    if (rx.diagnosis.length) sections.push(chipSection("Diagnosis", rx.diagnosis));
    if (rx.tests.length) sections.push(chipSection("Tests advised", rx.tests));
    if (rx.advice.length) {
      sections.push(el("section", { class: "extra" }, el("h4", { text: "Doctor's advice" }),
        el("ul", { class: "list" }, rx.advice.map((a) => el("li", { text: a })))));
    }
    if (rx.follow_up) sections.push(el("section", { class: "extra" }, el("h4", { text: "Follow-up" }), el("p", { text: rx.follow_up })));
    els.extras.replaceChildren(...sections);
  }

  function renderMedicine(med, index) {
    const facts = [
      ["Dose", med.dosage],
      ["How often", med.frequency],
      ["When", med.timing],
      ["For", med.duration],
    ].filter(([, v]) => v);
    return el("article", { class: "med" },
      el("div", { class: "med-head" },
        el("span", { class: "med-index", "aria-hidden": "true", text: index + 1 }),
        el("div", {},
          el("h4", { class: "med-name", text: med.name }),
          med.strength && el("p", { class: "med-strength", text: med.strength }))),
      facts.length && el("dl", { class: "med-facts" }, facts.map(([k, v]) => el("div", {}, el("dt", { text: k }), el("dd", { text: v })))),
      med.instructions && el("p", { class: "med-note", text: med.instructions }));
  }

  function chipSection(title, items) {
    return el("section", { class: "extra" }, el("h4", { text: title }),
      el("ul", { class: "chips" }, items.map((i) => el("li", { text: i }))));
  }

  // ---------------------------------------------------------------- audio player
  function setPlayIcon(playing) {
    els.play.replaceChildren(icon(playing ? "pause" : "play"));
    els.play.setAttribute("aria-label", playing ? "Pause" : "Play");
  }

  function stopAudio() {
    els.audio.pause();
    if (els.audio.src) els.audio.currentTime = 0;
    setPlayIcon(false);
    updateSeek();
  }

  function updateSeek() {
    const { currentTime, duration } = els.audio;
    const pct = duration ? (currentTime / duration) * 100 : 0;
    els.seek.value = String(pct);
    els.seek.style.setProperty("--pct", `${pct}%`);
    els.timeCurrent.textContent = formatTime(currentTime);
    els.timeTotal.textContent = formatTime(duration);
  }

  // ---------------------------------------------------------------- events
  function bindEvents() {
    els.cameraInput.addEventListener("change", () => handleFile(els.cameraInput.files[0]));
    els.fileInput.addEventListener("change", () => handleFile(els.fileInput.files[0]));
    els.changeFile.addEventListener("click", () => { clearFile(); els.fileInput.click(); });

    ["dragenter", "dragover"].forEach((type) =>
      els.dropzone.addEventListener(type, (e) => { e.preventDefault(); els.dropzone.classList.add("is-dragover"); }));
    ["dragleave", "drop"].forEach((type) =>
      els.dropzone.addEventListener(type, (e) => { e.preventDefault(); els.dropzone.classList.remove("is-dragover"); }));
    els.dropzone.addEventListener("drop", (e) => handleFile(e.dataTransfer?.files?.[0]));

    els.language.addEventListener("change", () => {
      fillVoices();
      savePrefs();
      if (state.summaryEn) switchLanguage(els.language.value);
    });
    els.voice.addEventListener("change", () => {
      savePrefs();
      if (state.current && state.current.lang === els.language.value) switchLanguage(els.language.value);
    });
    els.form.querySelectorAll('input[name="pace"]').forEach((r) =>
      r.addEventListener("change", () => { savePrefs(); if (state.current) switchLanguage(state.current.lang); }));
    els.sourceLanguage.addEventListener("change", () => { if (state.ocrText) resetAnalysis(); });
    els.resultLanguage.addEventListener("change", () => switchLanguage(els.resultLanguage.value));

    els.form.addEventListener("submit", (e) => { e.preventDefault(); runFull(); });
    els.retry.addEventListener("click", () => { hideError(); state.lastAction?.(); });
    els.startOver.addEventListener("click", () => {
      clearFile();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });

    els.play.addEventListener("click", () => {
      if (!els.audio.src) return;
      if (els.audio.paused) els.audio.play().catch(() => showError("Audio couldn't be played on this device. Try downloading it instead."));
      else els.audio.pause();
    });
    els.audio.addEventListener("play", () => setPlayIcon(true));
    els.audio.addEventListener("pause", () => setPlayIcon(false));
    els.audio.addEventListener("ended", () => setPlayIcon(false));
    ["timeupdate", "loadedmetadata", "durationchange"].forEach((t) => els.audio.addEventListener(t, updateSeek));
    els.seek.addEventListener("input", () => {
      if (els.audio.duration) els.audio.currentTime = (Number(els.seek.value) / 100) * els.audio.duration;
      updateSeek();
    });

    els.copySummary.addEventListener("click", async () => {
      if (!state.current) return;
      const label = els.copySummary.querySelector("span");
      try {
        await navigator.clipboard.writeText(state.current.text);
        label.textContent = "Copied";
      } catch {
        label.textContent = "Copy failed";
      }
      setTimeout(() => (label.textContent = "Copy text"), 1800);
    });

    if (navigator.share) {
      els.share.hidden = false;
      els.share.addEventListener("click", async () => {
        if (!state.current) return;
        const file = new File([state.current.blob], `prescription-${state.current.lang}.mp3`, { type: "audio/mpeg" });
        const data = { title: "My prescription", text: state.current.text };
        if (navigator.canShare?.({ files: [file] })) data.files = [file];
        try { await navigator.share(data); } catch { /* user cancelled */ }
      });
    }
  }

  bindEvents();
  setPlayIcon(false);
  loadLanguages();
})();
