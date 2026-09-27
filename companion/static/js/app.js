/* Perugia Companion — progressive enhancement for each screen.
   The app speaks more than it expects him to read, and never needs gestures. */
(() => {
  'use strict';

  const T = window.T;
  const CFG = window.CFG;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const fmt = (s, v) => s.replace(/\{(\w+)\}/g, (_, k) => (v[k] ?? ''));
  const SPEECH_TAG = CFG.lang === 'zh' ? 'zh-CN' : 'en-GB';
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const show = (el, on = true) => { if (el) el.hidden = !on; };

  /* ---------- voice ---------- */
  const hasSpeech = 'speechSynthesis' in window;
  const Voice = {
    enabled: () => localStorage.getItem('voice') !== 'off',
    set(on) {
      localStorage.setItem('voice', on ? 'on' : 'off');
      if (!on) Voice.stop();
    },
    pick(tag) {
      const voices = speechSynthesis.getVoices();
      const norm = (l) => l.replace('_', '-').toLowerCase();
      return voices.find((v) => norm(v.lang) === tag.toLowerCase())
        || voices.find((v) => norm(v.lang).startsWith(tag.slice(0, 2).toLowerCase()));
    },
    say(text, tag = SPEECH_TAG, force = false) {
      if (!hasSpeech || !text || (!force && !Voice.enabled())) return;
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = tag;
      const v = Voice.pick(tag);
      if (v) u.voice = v;
      u.rate = 0.88; // calm and unhurried
      speechSynthesis.speak(u);
    },
    stop() { if (hasSpeech) speechSynthesis.cancel(); },
  };
  if (hasSpeech) speechSynthesis.getVoices(); // voices load asynchronously; warm up

  // Browsers only allow speech after a tap. If the automatic announcement was
  // blocked, say it on the first touch (unless a button already started speaking).
  function sayOnFirstTouch(getText) {
    if (!hasSpeech) return;
    document.addEventListener('pointerdown', () => {
      setTimeout(() => { if (!speechSynthesis.speaking) Voice.say(getText()); }, 80);
    }, { once: true });
  }

  function initVoiceToggle() {
    const b = $('#voice-toggle');
    if (!b) return;
    const render = () => {
      const on = Voice.enabled();
      b.setAttribute('aria-pressed', String(on));
      $('use', b).setAttribute('href', on ? '#i-volume' : '#i-volume-off');
      $('span', b).textContent = on ? T.voice_on : T.voice_off;
    };
    b.addEventListener('click', () => {
      Voice.set(!Voice.enabled());
      render();
      if (Voice.enabled()) document.dispatchEvent(new Event('voice-on'));
    });
    render();
  }

  /* ---------- location ---------- */
  const Loc = {
    error: null, // 'denied' | 'unavailable' | null
    remember(c) { try { localStorage.setItem('lastLoc', JSON.stringify(c)); } catch (_) { /* private mode */ } },
    last(maxAgeMs = 10 * 60 * 1000) {
      try {
        const c = JSON.parse(localStorage.getItem('lastLoc') || 'null');
        return c && Date.now() - c.t < maxAgeMs ? c : null;
      } catch (_) { return null; }
    },
    from(p) {
      const c = { lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy, t: Date.now() };
      Loc.remember(c);
      return c;
    },
    get(maxAgeMs = 30000) {
      if (CFG.pretend) return Promise.resolve(null); // server uses the test position
      if (!('geolocation' in navigator)) { Loc.error = 'unavailable'; return Promise.resolve(Loc.last()); }
      const fresh = Loc.last(maxAgeMs);
      if (fresh) return Promise.resolve(fresh);
      return new Promise((resolve) => {
        navigator.geolocation.getCurrentPosition(
          (p) => { Loc.error = null; resolve(Loc.from(p)); },
          (err) => { Loc.error = err.code === 1 ? 'denied' : 'unavailable'; resolve(Loc.last()); },
          { enableHighAccuracy: true, timeout: 12000, maximumAge: 15000 },
        );
      });
    },
    query: (loc) => (loc ? `lat=${loc.lat.toFixed(6)}&lng=${loc.lng.toFixed(6)}` : ''),
  };
  const noLocationText = () => (Loc.error === 'denied' ? T.location_denied : T.no_location);

  function haversine(a, b) {
    const R = 6371000;
    const toRad = (d) => (d * Math.PI) / 180;
    const dLat = toRad(b.lat - a.lat);
    const dLng = toRad(b.lng - a.lng);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  // Shortest distance in metres from point p to a polyline [[lat, lng], ...].
  function distToPath(p, path) {
    if (!path || path.length < 2) return null;
    const kx = 111320 * Math.cos((p.lat * Math.PI) / 180);
    const ky = 110540;
    let best = Infinity;
    for (let k = 0; k < path.length - 1; k += 1) {
      const ax = (path[k][1] - p.lng) * kx; const ay = (path[k][0] - p.lat) * ky;
      const bx = (path[k + 1][1] - p.lng) * kx; const by = (path[k + 1][0] - p.lat) * ky;
      const dx = bx - ax; const dy = by - ay;
      const len2 = dx * dx + dy * dy;
      const t = len2 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2)) : 0;
      best = Math.min(best, Math.hypot(ax + t * dx, ay + t * dy));
    }
    return best;
  }

  async function getJSON(url, opts) {
    const r = await fetch(url, { headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, ...opts });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }

  function fmtDistance(m) {
    if (m >= 1000) return fmt(T.km_n, { n: Number((m / 1000).toFixed(1)) });
    return fmt(T.metres_n, { n: Math.max(10, Math.round(m / 10) * 10) });
  }

  function locNote(w) {
    const n = $('#loc-note');
    if (!n) return;
    if (CFG.pretend) n.textContent = T.test_mode;
    show(n, CFG.pretend);
  }

  /* ---------- large text overlay ---------- */
  let lastFocus = null;
  function openLarge(text) {
    const o = $('#large-overlay');
    lastFocus = document.activeElement;
    $('#large-text').textContent = text;
    o.hidden = false;
    $('[data-close-large]', o).focus();
  }
  function closeLarge() {
    $('#large-overlay').hidden = true;
    if (lastFocus) lastFocus.focus();
  }
  document.addEventListener('click', (e) => { if (e.target.closest('[data-close-large]')) closeLarge(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#large-overlay').hidden) closeLarge(); });

  /* ---------- home ---------- */
  function greeting() {
    const h = new Date().getHours();
    if (h < 12) return T.good_morning;
    return h < 18 ? T.good_afternoon : T.good_evening;
  }

  async function initHome() {
    const tick = () => {
      $('#greet').textContent = greeting();
      $('#clock').textContent = new Date().toLocaleTimeString(CFG.lang === 'zh' ? 'zh-CN' : 'en-GB',
        { hour: '2-digit', minute: '2-digit' });
    };
    tick();
    setInterval(tick, 20000);
    const label = $('#near-label');
    const place = $('#near-place');
    try {
      const loc = await Loc.get();
      const w = await getJSON(`/api/where?${Loc.query(loc)}`);
      if (w.no_location) {
        label.textContent = '';
        place.textContent = noLocationText();
        place.classList.add('where-small');
        return;
      }
      label.textContent = w.near ? T.you_are_near : '';
      place.textContent = w.near ? w.near.name : (w.in_perugia ? T.you_are_in_perugia : T.outside);
      locNote(w);
    } catch (_) {
      label.textContent = '';
      place.textContent = navigator.onLine ? T.you_are_in_perugia : T.offline;
    }
  }

  /* ---------- route choice ---------- */
  function routeFacts(r) {
    const names = { escalator: T.use_escalator, metro: T.use_metro, level: T.use_level, lift: T.use_lift };
    const parts = [r.stairs ? fmt(T.about_steps, { n: r.stairs }) : T.no_stairs];
    if (r.climb) parts.push(fmt(T.climb_n, { n: r.climb }));
    r.uses.forEach((u) => { if (names[u]) parts.push(names[u]); });
    return parts.join(' · ');
  }

  // "3× zebra crossing · 1× traffic lights · 80% on main streets"
  function safetyFacts(r) {
    const x = r.crossings;
    if (!x) return '';
    const parts = [];
    if (x.signals) parts.push(fmt(T.signals_n, { n: x.signals }));
    if (x.zebra) parts.push(fmt(T.zebra_n, { n: x.zebra }));
    if (x.crossing) parts.push(fmt(T.crossing_n, { n: x.crossing }));
    if (x.unmarked) parts.push(fmt(T.unmarked_n, { n: x.unmarked }));
    if (!parts.length) parts.push(T.no_crossings);
    if (typeof r.main_pct === 'number' && r.main_pct >= 0) parts.push(fmt(T.main_pct, { n: Math.min(100, r.main_pct) }));
    return parts.join(' · ');
  }

  function initRoute() {
    const root = $('#route-root');
    const dest = root.dataset.dest;
    const qs = root.dataset.qs ? `&${root.dataset.qs}` : ''; // one-off place from the search box
    const status = $('#route-status');
    const note = $('#route-note');
    const retry = $('#btn-retry');
    const taxi = $('#route-taxi');

    async function load() {
      status.textContent = T.loading_route;
      show(status); show(retry, false); show(note, false); show($('#route-options'), false);
      let data;
      try {
        const loc = await Loc.get();
        data = await getJSON(`/api/route?dest=${encodeURIComponent(dest)}${qs}&${Loc.query(loc)}`);
      } catch (_) {
        status.textContent = navigator.onLine ? T.route_error : T.offline;
        show(retry); show(taxi);
        return;
      }
      if (data.no_location) {
        status.textContent = noLocationText();
        show(retry); show(taxi);
        return;
      }
      if (data.taxi) {
        status.textContent = T.taxi_best;
        show(taxi);
        return;
      }
      if (data.easiest.steps.length === 1) {
        status.textContent = T.already_here;
        return;
      }
      show(status, false);
      const notes = [];
      if (data.engine === 'offline') notes.push(T.offline_directions);
      if (data.taxi_suggested) { notes.push(T.taxi_suggested); show(taxi); }
      if (notes.length) { note.textContent = notes.join(' '); show(note); }
      // One card per different route. The family's default comes first with "Recommended".
      const box = $('#route-options');
      const modes = ['easiest', 'safest', 'fastest'].filter((m) => data[m]);
      const preferred = modes.includes(data.default_mode) ? data.default_mode : modes[0];
      const order = [preferred, ...modes.filter((m) => m !== preferred)];
      const sameAs = (m) => {
        const r = data[m];
        return order.slice(0, order.indexOf(m)).find((o) => data[o].path.join('>') === r.path.join('>'));
      };
      order.forEach((m) => {
        const el = $(`.option[data-mode="${m}"]`, box);
        box.appendChild(el); // put the cards in this order
        const dup = sameAs(m);
        show(el, !dup);
        if (dup) return;
        const r = data[m];
        $('[data-f="minutes"]', el).textContent = r.minutes;
        $('[data-f="facts"]', el).textContent = routeFacts(r);
        const sf = safetyFacts(r);
        $('[data-f="safety"]', el).textContent = sf;
        show($('[data-f="safety"]', el), !!sf);
        const also = order.filter((o) => o !== m && sameAs(o) === m);
        const alsoEl = $('[data-f="also"]', el);
        alsoEl.textContent = also.length ? fmt(T.also_way, { x: also.map((o) => T[`word_${o}`]).join(T.also_and) }) : '';
        show(alsoEl, also.length > 0);
        const main = m === preferred;
        el.classList.toggle('option-main', main);
        show($('.badge', el), main);
        $('.option-cta', el).classList.toggle('option-cta-quiet', !main);
        el.href = `${root.dataset.navUrl}?mode=${m}${qs}`;
      });
      show($('#route-options'));
    }
    retry.addEventListener('click', load);
    load();
  }

  /* ---------- compass: which way the phone is facing ---------- */
  const Compass = {
    heading: null,
    started: false,
    listeners: [],
    // iPhone needs a tap before it will share the compass
    needsPermission: typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function',
    on(fn) { Compass.listeners.push(fn); },
    screenAngle: () => (screen.orientation && typeof screen.orientation.angle === 'number' ? screen.orientation.angle : (window.orientation || 0)),
    emit(raw) {
      const h = (raw + Compass.screenAngle() + 360) % 360;
      if (Compass.heading === null) Compass.heading = h;
      else {
        // smooth out the jitter; go the short way round 359 -> 0
        const d = ((h - Compass.heading + 540) % 360) - 180;
        Compass.heading = (Compass.heading + d * 0.3 + 360) % 360;
      }
      Compass.listeners.forEach((fn) => fn(Compass.heading));
    },
    onIOS(e) { if (typeof e.webkitCompassHeading === 'number') Compass.emit(e.webkitCompassHeading); },
    onAbsolute(e) { if (typeof e.alpha === 'number') Compass.emit(360 - e.alpha); },
    async start() {
      if (Compass.started) return true;
      try {
        if (Compass.needsPermission && await DeviceOrientationEvent.requestPermission() !== 'granted') return false;
        if ('ondeviceorientationabsolute' in window) {
          // Android Chrome: true north, no permission needed
          window.addEventListener('deviceorientationabsolute', Compass.onAbsolute);
        } else if ('DeviceOrientationEvent' in window) {
          // iPhone: webkitCompassHeading is already degrees from north
          window.addEventListener('deviceorientation', (e) => {
            if (typeof e.webkitCompassHeading === 'number') Compass.onIOS(e);
            else if (e.absolute) Compass.onAbsolute(e);
          });
        } else {
          return false;
        }
      } catch (_) {
        return false;
      }
      Compass.started = true;
      return true;
    },
  };

  /* ---------- navigation ---------- */
  function initNavigate() {
    const root = $('#nav-root');
    const { dest, mode } = root.dataset;
    const qs = root.dataset.qs ? `&${root.dataset.qs}` : '';
    const key = `nav:${dest}:${mode}${qs}`;
    const statusEl = $('#nav-status');
    const noteEl = $('#nav-note');
    const retry = $('#btn-retry');
    const gpsEl = $('#map-gps');
    const DIST_KINDS = ['walk', 'stairs', 'stairs_down', 'stairs_up', 'escalator', 'escalator_down', 'escalator_up', 'lift'];
    const LM = window.LiveMap;

    let r = null;
    let start = null; // where the route was planned from
    let i = 0;
    let stepStarted = Date.now();
    let noMetro = sessionStorage.getItem(`${key}:nometro`) === '1';
    let lastReroute = 0;
    let offCount = 0;
    let busy = false;
    let liveLeft = null; // metres left in this step, measured from GPS
    let lastHere = null; // latest GPS fix
    let hintSpokenFor = -1;
    let hintAt = 0;
    const hintEl = $('#face-hint');
    let warned = -1; // step index for which "soon" was already said
    let paths = [];

    /* ---- map ---- */
    const mapWrap = $('#map-wrap');
    const mapBtn = $('#map-toggle');
    const recentreBtn = $('#btn-recentre');
    let liveMap = null;
    const mapWanted = () => localStorage.getItem('map') !== 'off';
    function renderMapToggle() {
      const on = mapWanted();
      mapBtn.setAttribute('aria-pressed', String(on));
      $('span', mapBtn).textContent = on ? T.map_on : T.map_off;
      show(mapWrap, on);
      if (on && liveMap) setTimeout(() => liveMap.refresh(), 50);
    }
    mapBtn.addEventListener('click', () => {
      localStorage.setItem('map', mapWanted() ? 'off' : 'on');
      renderMapToggle();
    });
    if (LM) {
      liveMap = LM.create($('#nav-map'), {
        onFollowChange: (on) => show(recentreBtn, !on),
        labels: { zoomIn: T.zoom_in, zoomOut: T.zoom_out, facingUp: T.facing_up, northUp: T.north_up },
      });
      recentreBtn.addEventListener('click', () => liveMap && liveMap.recentre());
    }
    if (!liveMap) { show(mapWrap, false); show(mapBtn, false); } else renderMapToggle();

    const titleFor = (s) => {
      if (s.kind === 'walk') {
        return { straight: T.walk_straight, left: T.turn_left, right: T.turn_right, uturn: T.uturn }[s.arrow] || T.walk_straight;
      }
      return T[s.kind] || T.walk_straight;
    };
    const iconFor = (s) => (s.kind === 'walk' ? `#i-${s.arrow || 'straight'}` : `#i-${s.kind}`);
    const isLast = () => i === r.steps.length - 1;
    const stepMetres = (s) => (liveLeft !== null && s === r.steps[i] ? liveLeft : s.metres);
    const distFor = (s) => {
      const m = stepMetres(s);
      return DIST_KINDS.includes(s.kind) && m >= 10 ? fmtDistance(m) : '';
    };
    const spoken = (s) => {
      if (isLast()) return `${T.arrive}. ${fmt(T.arrived_at, { place: s.text })}`;
      const d = distFor(s);
      return `${titleFor(s)}. ${d ? `${d}. ` : ''}${s.text}`;
    };
    const sig = () => r.path.join('>');
    const note = (text) => { noteEl.textContent = text || ''; show(noteEl, !!text); };

    // Distance and time numbers only; cheap enough to run on every GPS fix.
    function renderLive() {
      const s = r.steps[i];
      const last = isLast();
      const d = distFor(s);
      $('#instr-dist').textContent = d;
      show($('#instr-dist'), !!d);
      const frac = liveLeft !== null && s.metres > 0 ? Math.min(1, liveLeft / Math.max(s.metres, 1)) : 1;
      const remaining = last ? 0 : Math.max(1, Math.round(s.minutes * frac + r.steps.slice(i + 1).reduce((a, x) => a + x.minutes, 0)));
      const pct = last ? 100 : Math.round((100 * (r.minutes - remaining)) / Math.max(1, r.minutes));
      $('#progress-fill').style.width = `${Math.max(0, Math.min(100, pct))}%`;
      $('#progress-label').textContent = last ? '' : fmt(T.min_left, { n: remaining });
    }

    function render(speak = true, prefix = '') {
      const s = r.steps[i];
      const last = isLast();
      $('#instr-arrow use').setAttribute('href', iconFor(s));
      $('#instr-title').textContent = titleFor(s);
      $('#instr-text').textContent = last ? fmt(T.arrived_at, { place: s.text }) : s.text;
      renderLive();
      const nx = r.steps[i + 1];
      show($('#next-up'), !!nx);
      $('#next-up').textContent = nx ? `${T.next}: ${titleFor(nx)}` : '';
      $('#step-count').textContent = fmt(T.step_of, { i: i + 1, n: r.steps.length });
      $('#btn-next-label').textContent = last ? T.finish : T.done_next;
      $('#btn-next use').setAttribute('href', last ? '#i-home' : '#i-check');
      show($('#btn-metro-closed'), s.kind === 'metro');
      if (liveMap) liveMap.setStep(i);
      sessionStorage.setItem(key, JSON.stringify({ i, sig: sig(), t: Date.now() }));
      if (speak) Voice.say(`${prefix ? `${prefix} ` : ''}${spoken(s)}`);
      facingHint();
    }

    function advance(to = i + 1) {
      if (to <= r.steps.length - 1 && to > i) {
        i = to;
        offCount = 0;
        liveLeft = null;
        stepStarted = Date.now();
        render();
        if (!liveMap || !mapWanted()) window.scrollTo({ top: 0, behavior: reducedMotion ? 'auto' : 'smooth' });
      }
    }

    function useRoute(route, { fresh, from }) {
      r = route;
      start = from || start;
      let saved = null;
      try { saved = JSON.parse(sessionStorage.getItem(key) || 'null'); } catch (_) { /* ignore */ }
      i = !fresh && saved && saved.sig === sig() && Date.now() - saved.t < 3 * 3600e3
        ? Math.min(saved.i, r.steps.length - 1) : 0;
      liveLeft = null;
      warned = -1;
      stepStarted = Date.now();
      paths = LM ? LM.stepPaths(r, start) : r.steps.map((x) => x.path || []);
      show(statusEl, false); show(retry, false);
      show($('#nav-step'));
      note(r.engine === 'offline' ? T.offline_directions : '');
      if (liveMap) { liveMap.setRoute(r, start); setTimeout(() => liveMap.refresh(), 50); }
    }

    async function load({ reroute = false, loc = null } = {}) {
      if (busy) return;
      busy = true;
      try {
        const here = loc || await Loc.get(reroute ? 0 : 30000);
        const url = `/api/route?dest=${encodeURIComponent(dest)}${qs}&mode=${mode}${noMetro ? '&no_metro=1' : ''}&${Loc.query(here)}`;
        let data = null;
        try { data = await getJSON(url); } catch (_) { data = null; }
        if (data && data.taxi) { window.location.replace(`/route/${encodeURIComponent(dest)}${qs ? `?${qs.slice(1)}` : ''}`); return; }
        if (data && data[mode]) {
          const from = data.start ? [data.start.lat, data.start.lng] : null;
          localStorage.setItem(`route:${key}`, JSON.stringify({ r: data[mode], from, t: Date.now() }));
          useRoute(data[mode], { fresh: reroute, from });
          render(true, reroute ? T.rerouting : '');
          return;
        }
        // No new route: keep the current one, or the last one saved on this phone.
        if (r) { note(data && data.no_location ? noLocationText() : T.offline); return; }
        let stored = null;
        try { stored = JSON.parse(localStorage.getItem(`route:${key}`) || 'null'); } catch (_) { /* ignore */ }
        if (stored && Date.now() - stored.t < 3 * 3600e3) {
          useRoute(stored.r, { fresh: false, from: stored.from });
          note(T.offline);
          render();
          return;
        }
        statusEl.textContent = data && data.no_location ? noLocationText() : (navigator.onLine ? T.route_error : T.offline);
        show(statusEl); show(retry);
      } finally {
        busy = false;
      }
    }

    /* ---- live tracking: every position fix goes through here ---- */
    function onPosition(here) {
      lastHere = here;
      if (liveMap) liveMap.update(here);
      facingHint();
      if (!r || busy || isLast()) return;
      const weak = here.acc > 40;
      if (gpsEl) { gpsEl.textContent = weak ? fmt(T.gps_weak, { n: Math.round(here.acc) }) : ''; show(gpsEl, weak); }
      if (weak) return; // show the dot, but don't make decisions on a bad fix
      const s = r.steps[i];

      if (s.kind === 'metro') {
        // Underground or on the train. Move on once he is out at the right station.
        const alight = { lat: s.lat, lng: s.lng };
        if (Date.now() - stepStarted > 90000 && haversine(here, alight) < 60) advance();
        return;
      }

      // Already at the end of this step, or of one a little further on?
      for (let k = Math.min(r.steps.length - 2, i + 2); k > i; k -= 1) {
        if (r.steps[k].kind !== 'metro' && haversine(here, r.steps[k]) < 20) { advance(k + 1); return; }
      }
      const proj = LM ? LM.project([here.lat, here.lng], paths[i]) : null;
      const toEnd = haversine(here, s);
      if (toEnd < 15 || (proj && proj.d < 30 && proj.remaining < 10)) { advance(); return; }

      // Live distance to the next turn.
      if (proj && proj.d < 40) {
        liveLeft = Math.round(proj.remaining);
        renderLive();
        const nx = r.steps[i + 1];
        if (nx && warned !== i && liveLeft < 35 && s.metres > 60) {
          warned = i;
          Voice.say(`${T.soon}: ${titleFor(nx)}.`);
        }
      }

      // Off the route? Find a new way from here.
      const nx = r.steps[i + 1];
      const dists = [proj && proj.d,
        nx && LM && nx.kind !== 'metro' && (LM.project([here.lat, here.lng], paths[i + 1]) || {}).d]
        .filter((d) => typeof d === 'number');
      if (!dists.length) return;
      offCount = Math.min(...dists) > 40 + here.acc / 2 ? offCount + 1 : 0;
      if (offCount >= 3 && Date.now() - lastReroute > 45000 && r.engine !== 'offline' && !CFG.pretend) {
        lastReroute = Date.now();
        offCount = 0;
        load({ reroute: true, loc: here });
      }
    }

    $('#btn-next').addEventListener('click', () => {
      if (!r) return;
      if (isLast()) {
        sessionStorage.removeItem(key);
        sessionStorage.removeItem(`${key}:nometro`);
        window.location.href = '/';
        return;
      }
      advance();
    });
    $('#btn-repeat').addEventListener('click', () => r && Voice.say(spoken(r.steps[i]), SPEECH_TAG, true));
    $('#btn-metro-closed').addEventListener('click', () => {
      noMetro = true;
      sessionStorage.setItem(`${key}:nometro`, '1');
      load({ reroute: true });
    });
    retry.addEventListener('click', () => load());
    document.addEventListener('voice-on', () => r && Voice.say(spoken(r.steps[i])));
    sayOnFirstTouch(() => (r ? spoken(r.steps[i]) : ''));

    // Keep the screen awake while walking.
    let wake = null;
    const lock = async () => { try { wake = await navigator.wakeLock?.request('screen'); } catch (_) { /* optional */ } };
    lock();
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') lock(); });

    /* ---- which way to face: compass arrow + "the way is to your left" ---- */
    function facingHint() {
      if (!hintEl) return;
      const h = liveMap ? liveMap.heading() : Compass.heading;
      const s = r && r.steps[i];
      if (!LM || !s || !lastHere || h === null || s.kind === 'metro' || s.kind === 'arrive' || lastHere.acc > 40) {
        show(hintEl, false);
        return;
      }
      if (Date.now() - hintAt < 250) return; // compass fires very often
      hintAt = Date.now();
      const here = [lastHere.lat, lastHere.lng];
      const path = paths[i];
      const proj = LM.project(here, path);
      let target = [s.lat, s.lng];
      if (proj && proj.d < 40) {
        const done = LM.pathLength(path) - proj.remaining;
        target = LM.pointAlong(path, Math.min(LM.pathLength(path), done + 15)).pt;
      }
      if (haversine(lastHere, { lat: target[0], lng: target[1] }) < 4) { show(hintEl, false); return; }
      const delta = ((LM.bearing(here, target) - h + 540) % 360) - 180;
      let key = 'face_ok';
      if (Math.abs(delta) > 135) key = 'face_back';
      else if (delta < -35) key = 'face_left';
      else if (delta > 35) key = 'face_right';
      $('span', hintEl).textContent = T[`${key}_short`];
      // the arrow points where to go, relative to the way he is facing
      $('.face-arrow', hintEl).style.transform = `rotate(${Math.round(delta)}deg)`;
      hintEl.classList.toggle('face-ok', key === 'face_ok');
      show(hintEl);
      // Say it once per step when he is facing the wrong way at the start of a step.
      if (key !== 'face_ok' && hintSpokenFor !== i && Date.now() - stepStarted < 20000 && (lastHere.speed || 0) < 0.6) {
        hintSpokenFor = i;
        Voice.say(T[key]);
      }
    }

    const compassBtn = $('#btn-compass');
    let compassSeen = false;
    Compass.on((h) => {
      if (liveMap) {
        const first = liveMap.heading() === null || !compassSeen;
        liveMap.setCompass(h);
        if (first && localStorage.getItem('mapRotate') !== 'off') liveMap.setRotateMode(true);
      }
      compassSeen = true;
      show(compassBtn, false);
      facingHint();
    });
    // Some phones share the compass straight away; iPhones only after a tap.
    Compass.start().then((ok) => {
      if (ok || !Compass.needsPermission) return;
      show(compassBtn);
      compassBtn.addEventListener('click', async () => { if (await Compass.start()) show(compassBtn, false); });
      // any first tap on the screen may also switch it on
      document.addEventListener('pointerup', () => Compass.start(), { once: true });
    });

    let watchId = null;
    if (!CFG.pretend && 'geolocation' in navigator) {
      watchId = navigator.geolocation.watchPosition((p) => {
        const here = Loc.from(p);
        here.heading = p.coords.heading;
        here.speed = p.coords.speed;
        onPosition(here);
      }, () => {}, { enableHighAccuracy: true, maximumAge: 3000 });
    }

    // Test mode: walk the route at a brisk pace so the map and steps can be tried at home.
    const simBtn = $('#btn-simulate');
    let simTimer = null;
    function stopSim() {
      clearInterval(simTimer);
      simTimer = null;
      if (simBtn) $('span', simBtn).textContent = T.simulate;
    }
    if (simBtn && LM) {
      simBtn.addEventListener('click', () => {
        if (simTimer) { stopSim(); return; }
        if (!r) return;
        $('span', simBtn).textContent = T.simulate_stop;
        let along = 0;
        let simStep = i;
        let metroWait = 0;
        simTimer = setInterval(() => {
          if (!r || isLast()) { stopSim(); return; }
          if (simStep !== i) { simStep = i; along = 0; metroWait = 0; }
          const s = r.steps[i];
          if (s.kind === 'metro') {
            // skip the ride after a few seconds, arriving at the station
            metroWait += 1;
            if (metroWait > 3) { stepStarted = 0; onPosition({ lat: s.lat, lng: s.lng, acc: 8, t: Date.now() }); }
            return;
          }
          const path = paths[i];
          along += 6; // metres per tick (about 4x walking speed)
          const at = LM.pointAlong(path, Math.min(along, LM.pathLength(path)));
          onPosition({ lat: at.pt[0], lng: at.pt[1], acc: 8, heading: at.heading, speed: 1.4, t: Date.now() });
        }, 1000);
      });
    }

    $('#btn-lost').addEventListener('click', () => {
      Voice.stop();
      stopSim();
      if (watchId !== null) navigator.geolocation.clearWatch(watchId);
      if (wake) wake.release().catch(() => {});
    });

    load();
  }

  /* ---------- I'm lost ---------- */
  async function initLost() {
    Voice.stop();
    let w = null;
    let loc = null;
    try {
      loc = await Loc.get(5000);
      w = await getJSON(`/api/where?${Loc.query(loc)}`);
    } catch (_) { /* keep the calm message anyway */ }

    let whereLine = T.you_are_in_perugia;
    if (w && w.no_location) {
      whereLine = noLocationText();
    } else if (w) {
      whereLine = w.near ? fmt(T.you_are_at, { place: w.near.name }) : (w.in_perugia ? T.you_are_in_perugia : T.outside);
      const m = w.home_m;
      $('#safe-home').textContent = m < 1000
        ? fmt(T.hotel_away, { n: Math.max(10, Math.round(m / 10) * 10) })
        : fmt(T.hotel_away_km, { n: Number((m / 1000).toFixed(1)) });
    } else if (!navigator.onLine) {
      whereLine = T.offline;
    }
    $('#safe-where').textContent = whereLine;
    const message = `${T.safe_title} ${whereLine} ${$('#safe-home').textContent} ${T.no_hurry}`;
    Voice.say(message);
    sayOnFirstTouch(() => message);

    $('#btn-share').addEventListener('click', async () => {
      const pos = (w && w.lat) ? w : (loc || Loc.last(60 * 60 * 1000));
      const url = pos ? `https://maps.google.com/?q=${pos.lat.toFixed(6)},${pos.lng.toFixed(6)}` : '';
      const text = `${T.share_msg} ${url}`;
      const n = $('#share-note');
      try {
        if (navigator.share) { await navigator.share({ title: T.app_name, text: T.share_msg, url }); return; }
        if (navigator.clipboard) {
          await navigator.clipboard.writeText(text);
          n.textContent = T.copied;
          show(n);
          return;
        }
      } catch (err) {
        if (err && err.name === 'AbortError') return; // user closed the share sheet
      }
      window.location.href = `sms:?&body=${encodeURIComponent(text)}`;
    });
  }

  /* ---------- translate ---------- */
  function initTranslate() {
    let current = '';
    const result = $('#result');
    const showResult = (src, it) => {
      current = it;
      $('#result-src').textContent = src;
      $('#result-it').textContent = it;
      $('#btn-play').disabled = false;
      $('#btn-large').disabled = false;
      show(result);
      result.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
    };

    $$('.phrase').forEach((b) => b.addEventListener('click', () => {
      showResult(b.dataset.src, b.dataset.it);
      Voice.say(b.dataset.it, 'it-IT', true);
    }));

    const form = $('#translate-form');
    const input = $('#say-text');
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      show(result);
      $('#result-src').textContent = text;
      $('#result-it').textContent = T.translating;
      $('#btn-play').disabled = true;
      $('#btn-large').disabled = true;
      try {
        const data = await getJSON('/api/translate', { method: 'POST', body: JSON.stringify({ text, from: CFG.lang }) });
        showResult(text, data.it);
      } catch (_) {
        $('#result-it').textContent = T.translate_fail;
        current = '';
      }
    });

    $('#btn-play').addEventListener('click', () => Voice.say(current, 'it-IT', true));
    $('#btn-large').addEventListener('click', () => { if (current) openLarge(current); });

    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const mic = $('#btn-mic');
    const micNote = $('#mic-note');
    if (!SR) {
      mic.addEventListener('click', () => { micNote.textContent = T.mic_unsupported; show(micNote); });
      return;
    }
    const rec = new SR();
    rec.lang = SPEECH_TAG;
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    let listening = false;
    rec.onstart = () => { listening = true; $('#mic-label').textContent = T.listening; mic.setAttribute('aria-pressed', 'true'); };
    rec.onend = () => { listening = false; $('#mic-label').textContent = T.speak; mic.setAttribute('aria-pressed', 'false'); };
    rec.onerror = () => { micNote.textContent = T.mic_unsupported; show(micNote); };
    rec.onresult = (e) => {
      input.value = e.results[0][0].transcript;
      form.requestSubmit();
    };
    mic.addEventListener('click', () => {
      show(micNote, false);
      Voice.stop();
      if (listening) rec.stop(); else rec.start();
    });
  }

  /* ---------- show to someone ---------- */
  function initShow() {
    const card = $('#show-card');
    $('#btn-play-card').addEventListener('click', () => {
      const text = $$('.show-line, .show-name, .show-addr', card).map((p) => p.textContent.trim()).join('. ');
      Voice.say(text, 'it-IT', true);
    });
  }

  /* ---------- where do I want to go: search + recent places ---------- */
  function placeURL(p) {
    const q = new URLSearchParams({ to_lat: p.lat.toFixed(6), to_lng: p.lng.toFixed(6), to_name: p.name, to_addr: p.detail || '' });
    return `/route/place?${q}`;
  }
  const recentPlaces = () => { try { return JSON.parse(localStorage.getItem('recentPlaces') || '[]'); } catch (_) { return []; } };
  function rememberPlace(p) {
    const list = recentPlaces().filter((x) => Math.abs(x.lat - p.lat) > 1e-4 || Math.abs(x.lng - p.lng) > 1e-4);
    list.unshift({ name: p.name, detail: p.detail || '', lat: p.lat, lng: p.lng });
    localStorage.setItem('recentPlaces', JSON.stringify(list.slice(0, 6)));
  }
  function placeButton(p, { remember }) {
    const a = document.createElement('a');
    a.className = 'btn btn-plain btn-left btn-place';
    a.href = placeURL(p);
    a.innerHTML = '<svg class="ic" aria-hidden="true"><use href="#i-pin"/></svg><span class="place-text"><span class="place-name"></span><span class="place-detail"></span></span>';
    $('.place-name', a).textContent = p.name;
    $('.place-detail', a).textContent = [p.detail, p.precise === false ? T.street_only : ''].filter(Boolean).join(' · ');
    if (remember) a.addEventListener('click', () => rememberPlace(p));
    return a;
  }

  function initChoose() {
    const recent = recentPlaces();
    if (recent.length) {
      const list = $('#recent-list');
      recent.forEach((p) => list.appendChild(placeButton(p, { remember: true })));
      show($('#recent'));
    }
    const form = $('#place-form');
    const input = $('#place-q');
    const status = $('#place-status');
    const results = $('#place-results');
    const say = (text) => { status.textContent = text || ''; show(status, !!text); };

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const q = input.value.trim();
      if (q.length < 2) { input.focus(); return; }
      results.textContent = '';
      say(T.searching);
      try {
        const d = await getJSON(`/api/search?q=${encodeURIComponent(q)}`);
        if (!d.results.length) { say(T.no_results); return; }
        say('');
        d.results.forEach((p) => results.appendChild(placeButton(p, { remember: true })));
        results.firstChild.focus();
      } catch (_) {
        say(T.search_fail);
      }
    });
    // A pasted link or address searches straight away.
    input.addEventListener('paste', () => setTimeout(() => form.requestSubmit(), 0));
    $('#btn-paste').addEventListener('click', async () => {
      try {
        const text = await navigator.clipboard.readText();
        if (!text.trim()) throw new Error('empty');
        input.value = text.trim();
        form.requestSubmit();
      } catch (_) {
        say(T.paste_fail);
        input.focus();
      }
    });
  }

  /* ---------- family setup ---------- */
  function initSetup() {
    $$('[data-geo]').forEach((fs) => {
      const box = $('[data-geo-results]', fs);
      const lat = $('[data-geo-lat]', fs);
      const lng = $('[data-geo-lng]', fs);
      const mapLink = $('[data-geo-map]', fs);
      const updateMap = () => {
        const a = parseFloat(lat.value); const b = parseFloat(lng.value);
        const ok = Number.isFinite(a) && Number.isFinite(b);
        show(mapLink, ok);
        if (ok) mapLink.href = `https://www.openstreetmap.org/?mlat=${a}&mlon=${b}#map=18/${a}/${b}`;
      };
      const setPos = (a, b, label) => {
        lat.value = a.toFixed(6);
        lng.value = b.toFixed(6);
        box.textContent = label;
        updateMap();
      };
      lat.addEventListener('input', updateMap);
      lng.addEventListener('input', updateMap);
      updateMap();

      $('[data-geo-find]', fs).addEventListener('click', async () => {
        const q = $('[data-geo-q]', fs).value.trim();
        if (q.length < 3) return;
        box.textContent = 'Searching… / 正在搜索…';
        try {
          const d = await getJSON(`/api/geocode?q=${encodeURIComponent(q)}`);
          box.textContent = '';
          if (!d.results.length) {
            box.textContent = 'Not found near Perugia. Try "Via …, number, Perugia", or stand there and press "I am here". / 在佩鲁贾附近没有找到。';
            return;
          }
          const hint = document.createElement('p');
          hint.textContent = 'Choose the right one: / 请选择正确的地址：';
          box.appendChild(hint);
          d.results.forEach((res) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'phrase';
            b.textContent = res.precise ? res.name : `${res.name} — street only, not the exact door / 仅街道，不是具体门牌`;
            b.addEventListener('click', () => setPos(res.lat, res.lng, `Selected / 已选择: ${res.name}${res.precise ? '' : ' — please check the map / 请在地图上核对'}`));
            box.appendChild(b);
          });
        } catch (_) {
          box.textContent = 'Search is not available right now. / 暂时无法搜索。';
        }
      });

      $('[data-geo-here]', fs).addEventListener('click', () => {
        if (!('geolocation' in navigator)) { box.textContent = 'Location is not available. / 无法定位。'; return; }
        box.textContent = 'Finding your position… / 正在定位…';
        navigator.geolocation.getCurrentPosition(
          (p) => setPos(p.coords.latitude, p.coords.longitude,
            `Using where you are now (accurate to about ${Math.round(p.coords.accuracy)} m). / 已使用您现在的位置。`),
          () => { box.textContent = 'Could not get your position. Allow location for this site. / 无法获取位置，请允许定位。'; },
          { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 },
        );
      });
    });
    const share = $('#btn-share-link');
    if (share) {
      share.addEventListener('click', async () => {
        const url = share.dataset.link;
        try {
          if (navigator.share) { await navigator.share({ title: 'Perugia Companion setup', url }); return; }
          await navigator.clipboard.writeText(url);
          $('span', share).textContent = 'Copied / 已复制';
        } catch (_) {
          $('#setup-link').select();
        }
      });
    }
  }

  /* ---------- boot ---------- */
  const pages = {
    home: initHome, route: initRoute, navigate: initNavigate, lost: initLost,
    translate: initTranslate, show: initShow, setup: initSetup, choose: initChoose,
  };
  document.addEventListener('DOMContentLoaded', () => {
    initVoiceToggle();
    const init = pages[document.body.dataset.page];
    if (init) init();
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
})();
