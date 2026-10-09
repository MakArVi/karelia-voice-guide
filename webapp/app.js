(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = tg ? tg.initData : "";
  const inTelegram = Boolean(initData);  // вне Telegram скрипт тоже создаёт WebApp, но без данных
  const $ = (id) => document.getElementById(id);
  const IDLE = "Нажмите на микрофон и говорите";
  const MAX_SECONDS = 30;

  // ---------- Telegram ----------
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme() {
    const dark = inTelegram ? tg.colorScheme === "dark" : systemDark.matches;
    document.documentElement.classList.toggle("dark", dark);
  }
  if (!inTelegram && systemDark.addEventListener) systemDark.addEventListener("change", applyTheme);
  if (tg) {
    tg.ready();
    tg.expand();
    try { if (tg.isVersionAtLeast && tg.isVersionAtLeast("7.7")) tg.disableVerticalSwipes(); } catch (e) { /* старый клиент */ }
    try { tg.setHeaderColor("bg_color"); tg.setBackgroundColor("bg_color"); } catch (e) { /* старый клиент */ }
    tg.onEvent("themeChanged", applyTheme);
    tg.BackButton.onClick(onBack);
  }
  applyTheme();

  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (["light", "medium", "heavy"].includes(kind)) tg.HapticFeedback.impactOccurred(kind);
      else tg.HapticFeedback.notificationOccurred(kind);
    } catch (e) { /* нет поддержки */ }
  }

  function openLink(url) {
    if (tg && tg.openLink) tg.openLink(url);
    else window.open(url, "_blank", "noopener");
  }

  async function api(path, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    if (initData) headers["X-Telegram-Init-Data"] = initData;
    const response = await fetch(path, Object.assign({}, options, { headers }));
    let data = null;
    try { data = await response.json(); } catch (e) { /* не JSON */ }
    if (!response.ok) throw new Error((data && data.detail) || `Сервер ответил ошибкой ${response.status}`);
    return data;
  }

  // ---------- интерфейс ----------
  const statusEl = $("status");
  const list = $("messages");
  let busy = false;
  let recording = false;

  function setStatus(text) { statusEl.textContent = text; }

  function toast(text) {
    const el = $("toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, 4500);
  }

  function showTab(name) {
    document.querySelectorAll(".tab").forEach((tab) => {
      const on = tab.dataset.tab === name;
      tab.classList.toggle("active", on);
      tab.setAttribute("aria-selected", String(on));
    });
    $("screen-chat").classList.toggle("active", name === "chat");
    $("screen-map").classList.toggle("active", name === "map");
    $("dock").hidden = name !== "chat";
    if (tg) name === "map" ? tg.BackButton.show() : tg.BackButton.hide();
    if (name === "map") initMap();
  }
  document.querySelectorAll(".tab").forEach((tab) => { tab.onclick = () => showTab(tab.dataset.tab); });

  function onBack() {
    if (!$("sheet").hidden) closeSheet();
    else showTab("chat");
  }

  function scrollDown() { requestAnimationFrame(() => { list.scrollTop = list.scrollHeight; }); }

  function hideWelcome() { const w = $("welcome"); if (w) w.remove(); }

  function addMessage(role, text) {
    hideWelcome();
    const el = document.createElement("div");
    el.className = `msg ${role}`;
    el.textContent = text;
    list.appendChild(el);
    scrollDown();
    return el;
  }

  function addTyping() {
    const el = document.createElement("div");
    el.className = "msg bot typing";
    el.innerHTML = "<span></span><span></span><span></span>";
    list.appendChild(el);
    scrollDown();
    return el;
  }

  function sourcesLine(sources) {
    const box = document.createElement("div");
    box.append("Источники: ");
    sources.forEach((s, i) => {
      const a = document.createElement("a");
      a.href = s.url;
      a.textContent = s.label;
      a.onclick = (e) => { e.preventDefault(); openLink(s.url); };
      box.append(a);
      if (i < sources.length - 1) box.append(", ");
    });
    return box;
  }

  function renderAnswer(data) {
    const el = addMessage("bot", data.answer);
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    if (data.audio_url) {
      const listen = document.createElement("button");
      listen.className = "mini";
      listen.textContent = "▶ Прослушать";
      listen.onclick = () => { unlockAudio(); play(data.audio_url); };
      actions.appendChild(listen);
    }
    if (data.place) {
      const onMap = document.createElement("button");
      onMap.className = "mini";
      onMap.textContent = `📍 ${data.place.name} на карте`;
      onMap.onclick = () => { showTab("map"); focusPlace(data.place.id); };
      actions.appendChild(onMap);
    }
    if (actions.children.length) el.appendChild(actions);
    if (data.sources && data.sources.length) {
      const src = sourcesLine(data.sources.slice(0, 3));
      src.className = "msg-sources";
      el.appendChild(src);
    }
    scrollDown();
  }

  // ---------- звук ----------
  const player = $("player");
  const SILENCE = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=";
  let unlocked = false;

  // iOS разрешает звук только после касания — «разогреваем» плеер в момент нажатия
  function unlockAudio() {
    if (unlocked) return;
    unlocked = true;
    try { player.src = SILENCE; const p = player.play(); if (p) p.catch(() => {}); } catch (e) { /* ничего */ }
  }

  function play(url) {
    player.src = url;
    const p = player.play();
    setSpeaking(true);
    if (p) p.catch(() => { setSpeaking(false); toast("Нажмите «Прослушать», чтобы включить звук"); });
  }

  function setSpeaking(on) {
    $("stopAudio").classList.toggle("active", on);
    if (on) setStatus("Отвечаю голосом…");
    else if (!busy && !recording) setStatus(IDLE);
  }
  player.onended = () => setSpeaking(false);
  player.onpause = () => setSpeaking(false);
  player.onerror = () => { if (player.src !== SILENCE) setSpeaking(false); };
  $("stopAudio").onclick = () => player.pause();

  // ---------- вопросы ----------
  async function run(request, firstStatus) {
    busy = true;
    $("mic").classList.add("busy");
    const steps = [firstStatus, "Ищу ответ…", "Готовлю озвучку…"];
    let step = 0;
    setStatus(steps[0]);
    const stepper = setInterval(() => { if (step < steps.length - 1) setStatus(steps[++step]); }, 2500);
    const typing = addTyping();
    try {
      const data = await request();
      typing.remove();
      if (data.error) {
        addMessage("bot", data.error).classList.add("error");
        haptic("warning");
        return data;
      }
      renderAnswer(data);
      haptic("success");
      if (data.audio_url) play(data.audio_url);
      return data;
    } catch (e) {
      typing.remove();
      addMessage("bot", e.message || "Что-то пошло не так. Попробуйте ещё раз.").classList.add("error");
      haptic("error");
      return null;
    } finally {
      clearInterval(stepper);
      busy = false;
      $("mic").classList.remove("busy");
      if (!recording && player.paused) setStatus(IDLE);
    }
  }

  function ask(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    unlockAudio();
    player.pause();
    addMessage("user", text);
    return run(() => api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }), "Думаю…");
  }

  document.querySelectorAll("#suggestions .chip").forEach((chip) => { chip.onclick = () => ask(chip.textContent); });

  // ---------- запись голоса ----------
  let recorder = null;
  let stream = null;
  let chunks = [];
  let cancelled = false;
  let startedAt = 0;
  let timer = null;

  function pickMime() {
    if (!window.MediaRecorder || !MediaRecorder.isTypeSupported) return "";
    return ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4", "audio/webm"]
      .find((t) => MediaRecorder.isTypeSupported(t)) || "";
  }

  async function startRecording() {
    if (busy) return;
    unlockAudio();
    player.pause();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      toast("Микрофон здесь недоступен — напишите вопрос или отправьте голосовое прямо в чат с ботом.");
      switchToText();
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      toast("Нет доступа к микрофону. Разрешите его для Telegram или напишите вопрос текстом.");
      switchToText();
      return;
    }
    const mime = pickMime();
    try { recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined); }
    catch (e) { recorder = new MediaRecorder(stream); }
    chunks = [];
    cancelled = false;
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    recorder.onstop = onRecordingStop;
    recorder.start(250);
    recording = true;
    startedAt = Date.now();
    $("mic").classList.add("recording");
    $("mic").setAttribute("aria-label", "Отправить вопрос");
    $("toKeyboard").hidden = true;
    $("cancelRec").hidden = false;
    haptic("medium");
    tick();
    timer = setInterval(tick, 250);
  }

  function tick() {
    const s = Math.floor((Date.now() - startedAt) / 1000);
    setStatus(`Слушаю… 0:${String(s).padStart(2, "0")} — нажмите ещё раз, чтобы отправить`);
    if (s >= MAX_SECONDS) stopRecording(false);
  }

  function stopRecording(cancel) {
    if (!recording) return;
    cancelled = cancel;
    recording = false;
    clearInterval(timer);
    $("mic").classList.remove("recording");
    $("mic").setAttribute("aria-label", "Задать вопрос голосом");
    $("toKeyboard").hidden = false;
    $("cancelRec").hidden = true;
    haptic("light");
    try { recorder.stop(); } catch (e) { onRecordingStop(); }
  }

  async function onRecordingStop() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    if (cancelled) { setStatus(IDLE); return; }
    const type = (recorder && recorder.mimeType) || "audio/webm";
    const blob = new Blob(chunks, { type });
    if (blob.size < 1500 || Date.now() - startedAt < 700) {
      toast("Слишком коротко: нажмите на микрофон, задайте вопрос и нажмите ещё раз.");
      setStatus(IDLE);
      return;
    }
    const bubble = addMessage("user", "🎙 …");
    bubble.classList.add("pending");
    const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
    const form = new FormData();
    form.append("audio", blob, `voice.${ext}`);
    const data = await run(() => api("/api/voice", { method: "POST", body: form }), "Распознаю речь…");
    bubble.classList.remove("pending");
    bubble.textContent = data && data.question ? `🎙 ${data.question}` : "🎙 Голосовой вопрос";
  }

  $("mic").onclick = () => (recording ? stopRecording(false) : startRecording());
  $("cancelRec").onclick = () => stopRecording(true);

  // ---------- текстовый режим ----------
  function switchToText() {
    $("voiceRow").hidden = true;
    $("textRow").hidden = false;
    setStatus("Напишите вопрос о Карелии");
    $("textInput").focus();
  }
  function switchToVoice() {
    $("textRow").hidden = true;
    $("voiceRow").hidden = false;
    setStatus(IDLE);
  }
  $("toKeyboard").onclick = switchToText;
  $("toVoice").onclick = switchToVoice;
  $("textRow").onsubmit = (e) => {
    e.preventDefault();
    const value = $("textInput").value;
    $("textInput").value = "";
    ask(value);
  };

  // ---------- карта ----------
  let places = [];
  let map = null;
  const markers = {};
  let current = null;

  function cssColor(variable) {
    const probe = document.createElement("span");
    probe.style.color = `var(${variable})`;
    document.body.appendChild(probe);
    const color = getComputedStyle(probe).color;
    probe.remove();
    return color;
  }

  async function loadPlaces() {
    try {
      places = await api("/api/places");
    } catch (e) {
      toast("Не удалось загрузить список мест");
      return;
    }
    const strip = $("placeStrip");
    strip.innerHTML = "";
    places.forEach((p) => {
      const chip = document.createElement("button");
      chip.className = "place-chip";
      chip.textContent = `${p.emoji} ${p.name}`;
      chip.onclick = () => focusPlace(p.id);
      strip.appendChild(chip);
    });
    if (map) places.forEach(addMarker);
  }

  function initMap() {
    if (map) { setTimeout(() => map.invalidateSize(), 60); return; }
    if (!window.L) { toast("Карта не загрузилась"); return; }
    map = L.map("map", { zoomControl: true, attributionControl: false, worldCopyJump: false })
      .setView([63.6, 33.2], 5);
    L.control.attribution({ position: "topright", prefix: false })
      .addAttribution('© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>')
      .addTo(map);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(map);
    const accent = cssColor("--accent");
    fetch("data/karelia.geojson")
      .then((r) => r.json())
      .then((geo) => {
        const layer = L.geoJSON(geo, {
          interactive: false,
          style: { color: accent, weight: 2, opacity: 0.9, fillColor: accent, fillOpacity: 0.07 },
        }).addTo(map);
        if (!current) map.fitBounds(layer.getBounds(), { padding: [12, 12] });
      })
      .catch(() => { /* без контура тоже можно */ });
    places.forEach(addMarker);
    setTimeout(() => map.invalidateSize(), 60);
  }

  function addMarker(p) {
    if (markers[p.id]) return;
    const icon = L.divIcon({
      className: "pin",
      html: `<div class="pin-dot" style="--c:${p.color}">${p.emoji}</div>`,
      iconSize: [36, 36],
      iconAnchor: [18, 18],
    });
    markers[p.id] = L.marker([p.lat, p.lon], { icon, title: p.name, keyboard: true })
      .addTo(map)
      .on("click", () => openSheet(p.id));
  }

  function focusPlace(id) {
    const p = places.find((x) => x.id === id);
    if (!p) return;
    initMap();
    current = p;
    map.flyTo([p.lat, p.lon], 9, { duration: 0.8 });
    openSheet(id);
  }

  function highlight(id) {
    Object.entries(markers).forEach(([key, marker]) => {
      const dot = marker.getElement() && marker.getElement().querySelector(".pin-dot");
      if (dot) dot.classList.toggle("active", key === id);
    });
  }

  function openSheet(id) {
    const p = places.find((x) => x.id === id);
    if (!p) return;
    current = p;
    $("sheetEmoji").textContent = p.emoji;
    $("sheetEmoji").style.setProperty("--c", p.color);
    $("sheetTitle").textContent = p.title;
    $("sheetMeta").textContent = `${p.category} · ${p.location}`;
    $("sheetText").textContent = p.short;
    const sources = $("sheetSources");
    sources.innerHTML = "";
    if (p.sources.length) sources.appendChild(sourcesLine(p.sources));
    $("sheet").hidden = false;
    $("placeStrip").hidden = true;
    highlight(id);
    haptic("light");
  }

  function closeSheet() {
    $("sheet").hidden = true;
    $("placeStrip").hidden = false;
    highlight(null);
  }
  $("sheetClose").onclick = closeSheet;

  $("sheetListen").onclick = async () => {
    if (!current) return;
    unlockAudio();
    const btn = $("sheetListen");
    btn.disabled = true;
    try {
      const data = await api("/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ place_id: current.id }),
      });
      if (data.audio_url) play(data.audio_url);
    } catch (e) {
      toast(e.message);
    } finally {
      btn.disabled = false;
    }
  };
  $("sheetAsk").onclick = () => { if (current) { showTab("chat"); ask(`Расскажи про ${current.name}`); } };
  $("sheetRoute").onclick = () => {
    if (current) openLink(`https://yandex.ru/maps/?rtext=~${current.lat},${current.lon}&rtt=auto`);
  };

  // ---------- старт ----------
  setStatus(IDLE);
  loadPlaces();
  api("/api/health").then((h) => {
    $("modeLabel").textContent = h.mode === "claude" ? "голосовой путеводитель · Claude" : "голосовой путеводитель";
  }).catch(() => {});
})();
