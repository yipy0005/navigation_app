/* Live route map for the navigation screen (Leaflet, bundled locally).

   Shows the route, the part already walked (grey), the current step (thick),
   the Minimetrò ride (dotted), the destination flag and a blue "you" dot with
   GPS accuracy and walking direction. Follows him automatically; if he moves
   the map by hand, a "Show me" button brings it back (and it returns by itself
   after a short while). */
(() => {
  'use strict';

  const COLORS = { route: '#0B5563', current: '#0B5563', walked: '#8C8C8C', casing: '#FFFFFF', metro: '#0B5563' };
  const FOLLOW_ZOOM = 18;
  const REFOLLOW_MS = 12000;

  function metres(a, b) {
    const R = 6371000;
    const toRad = (d) => (d * Math.PI) / 180;
    const dLat = toRad(b[0] - a[0]);
    const dLng = toRad(b[1] - a[1]);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  /* Nearest point on a polyline. Returns the segment, the point, the distance
     to it and how many metres of the polyline are left after it. */
  function project(p, path) {
    if (!path || path.length < 2) return null;
    const kx = 111320 * Math.cos((p[0] * Math.PI) / 180);
    const ky = 110540;
    let best = null;
    for (let k = 0; k < path.length - 1; k += 1) {
      const ax = (path[k][1] - p[1]) * kx; const ay = (path[k][0] - p[0]) * ky;
      const bx = (path[k + 1][1] - p[1]) * kx; const by = (path[k + 1][0] - p[0]) * ky;
      const dx = bx - ax; const dy = by - ay;
      const len2 = dx * dx + dy * dy;
      const t = len2 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2)) : 0;
      const d = Math.hypot(ax + t * dx, ay + t * dy);
      if (!best || d < best.d) best = { k, t, d };
    }
    const a = path[best.k]; const b = path[best.k + 1];
    best.pt = [a[0] + (b[0] - a[0]) * best.t, a[1] + (b[1] - a[1]) * best.t];
    let rest = metres(best.pt, b);
    for (let k = best.k + 1; k < path.length - 1; k += 1) rest += metres(path[k], path[k + 1]);
    best.remaining = rest;
    return best;
  }

  function pathLength(path) {
    let m = 0;
    for (let k = 0; k < (path || []).length - 1; k += 1) m += metres(path[k], path[k + 1]);
    return m;
  }

  // Point `along` metres into a polyline, plus the direction of travel there.
  function pointAlong(path, along) {
    let left = along;
    for (let k = 0; k < path.length - 1; k += 1) {
      const seg = metres(path[k], path[k + 1]);
      if (left <= seg || k === path.length - 2) {
        const t = seg ? Math.min(1, left / seg) : 1;
        const a = path[k]; const b = path[k + 1];
        const heading = (Math.atan2((b[1] - a[1]) * Math.cos((a[0] * Math.PI) / 180), b[0] - a[0]) * 180) / Math.PI;
        return { pt: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], heading: (heading + 360) % 360 };
      }
      left -= seg;
    }
    return { pt: path[path.length - 1], heading: null };
  }

  // Every step gets a drawable line, even offline steps that have no geometry.
  function stepPaths(route, start) {
    let prev = start;
    return route.steps.map((s) => {
      const end = [s.lat, s.lng];
      const path = s.path && s.path.length >= 2 ? s.path : (prev ? [prev, end] : [end, end]);
      prev = end;
      return path;
    });
  }

  function create(el, { onFollowChange, labels = {} } = {}) {
    if (!window.L) return null;
    const L = window.L;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* The Leaflet map lives in an inner square as big as the box's diagonal,
       so it can turn ("facing up") without showing empty corners. Controls
       sit outside it and never turn. */
    const inner = document.createElement('div');
    inner.className = 'map-rot';
    el.appendChild(inner);
    const controls = document.createElement('div');
    controls.className = 'map-controls';
    controls.innerHTML = `
      <button type="button" class="map-ctl" data-zoom="in" aria-label="${labels.zoomIn || 'Zoom in'}">+</button>
      <button type="button" class="map-ctl" data-zoom="out" aria-label="${labels.zoomOut || 'Zoom out'}">−</button>
      <button type="button" class="map-ctl map-north" data-rotate aria-pressed="false" hidden
              aria-label="${labels.facingUp || 'Facing up'}"><span class="map-north-n">N</span></button>`;
    el.appendChild(controls);
    const attribution = document.createElement('div');
    attribution.className = 'map-attrib';
    attribution.innerHTML = '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>';
    el.appendChild(attribution);

    const map = L.map(inner, { zoomControl: false, attributionControl: false, zoomSnap: 0.5, tapTolerance: 20 });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19 }).addTo(map);

    const routeLayer = L.layerGroup().addTo(map);
    const meLayer = L.layerGroup().addTo(map);
    let route = null;
    let paths = [];
    let stepIdx = 0;
    let follow = true;
    let lastHere = null;
    let moveHeading = null; // from walking (GPS)
    let compass = null; // from the phone's compass
    let rotateMode = false;
    let rotation = 0; // degrees the map is turned (cumulative, so 359 -> 1 turns 2 degrees, not 358)
    let refollowTimer = null;
    let meMarker = null;
    let accCircle = null;
    let frame = null;

    const heading = () => (compass !== null ? compass : moveHeading);

    function sizeInner() {
      const w = el.clientWidth; const h = el.clientHeight;
      const d = Math.ceil(Math.hypot(w, h)) + 4;
      inner.style.width = `${d}px`;
      inner.style.height = `${d}px`;
      map.invalidateSize(false);
    }
    const pad = () => {
      const d = inner.clientWidth || 0;
      return [Math.max(0, (d - el.clientWidth) / 2) + 24, Math.max(0, (d - el.clientHeight) / 2) + 24];
    };

    function applyRotation() {
      frame = null;
      inner.style.transform = `translate(-50%, -50%) rotate(${-rotation}deg)`;
      const needle = controls.querySelector('.map-north-n');
      if (needle) needle.style.transform = `rotate(${-rotation}deg)`;
      // The dot's cone is drawn in map directions, so it shows the true heading either way.
      const cone = meMarker && meMarker.getElement() && meMarker.getElement().querySelector('.me-cone');
      const hd = heading();
      if (cone) {
        cone.hidden = hd === null;
        if (hd !== null) cone.style.transform = `rotate(${hd}deg)`;
      }
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(applyRotation); };

    function turnTo(target) {
      // nearest equivalent angle, so the map never spins the long way round
      const delta = ((target - rotation + 540) % 360) - 180;
      rotation += delta;
      schedule();
    }

    const setFollow = (on) => {
      follow = on;
      if (onFollowChange) onFollowChange(on);
      clearTimeout(refollowTimer);
      if (!on) refollowTimer = setTimeout(() => { setFollow(true); centre(); }, REFOLLOW_MS);
    };
    map.on('dragstart', () => setFollow(false));

    function centre() {
      if (!lastHere) return;
      map.setView(lastHere, Math.max(map.getZoom(), FOLLOW_ZOOM - 1), { animate: !reduced });
    }

    controls.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      if (b.dataset.zoom === 'in') map.zoomIn();
      if (b.dataset.zoom === 'out') map.zoomOut();
      if ('rotate' in b.dataset) api.setRotateMode(!rotateMode, true);
    });

    function line(latlngs, opts) {
      L.polyline(latlngs, { interactive: false, lineCap: 'round', lineJoin: 'round', ...opts }).addTo(routeLayer);
    }

    function draw(split) {
      routeLayer.clearLayers();
      paths.forEach((path, k) => {
        const s = route.steps[k];
        if (s.kind === 'arrive') return;
        if (s.kind === 'metro') {
          line(path, { color: COLORS.casing, weight: 10, opacity: 0.9 });
          line(path, { color: COLORS.metro, weight: 5, dashArray: '1 12', opacity: k < stepIdx ? 0.35 : 1 });
          path.forEach((pt) => L.circleMarker(pt, { radius: 5, color: COLORS.metro, weight: 3, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(routeLayer));
          return;
        }
        if (k < stepIdx) {
          line(path, { color: COLORS.walked, weight: 7, opacity: 0.8 });
        } else if (k === stepIdx) {
          const walked = split ? path.slice(0, split.k + 1).concat([split.pt]) : [path[0]];
          const ahead = split ? [split.pt].concat(path.slice(split.k + 1)) : path;
          if (walked.length > 1) line(walked, { color: COLORS.walked, weight: 8, opacity: 0.8 });
          line(ahead, { color: COLORS.casing, weight: 14, opacity: 0.95 });
          line(ahead, { color: COLORS.current, weight: 9 });
        } else {
          line(path, { color: COLORS.casing, weight: 11, opacity: 0.9 });
          line(path, { color: COLORS.route, weight: 6, opacity: 0.75 });
        }
      });
      const cur = route.steps[stepIdx];
      if (cur && cur.kind !== 'arrive') {
        L.circleMarker([cur.lat, cur.lng], { radius: 9, color: COLORS.current, weight: 4, fillColor: '#fff', fillOpacity: 1, interactive: false }).addTo(routeLayer);
      }
      const last = route.steps[route.steps.length - 1];
      L.marker([last.lat, last.lng], {
        interactive: false, keyboard: false,
        icon: L.divIcon({ className: 'dest-icon', iconSize: [44, 44], iconAnchor: [22, 40], html: '<svg class="ic" aria-hidden="true"><use href="#i-arrive"/></svg>' }),
      }).addTo(routeLayer);
    }

    sizeInner();
    if (window.ResizeObserver) new ResizeObserver(() => sizeInner()).observe(el);

    const api = {
      map,
      setRoute(r, start) {
        route = r;
        paths = stepPaths(r, start);
        stepIdx = 0;
        draw(null);
        const all = paths.flat();
        if (lastHere) all.push(lastHere);
        if (all.length) {
          const [px, py] = pad();
          map.fitBounds(L.latLngBounds(all), { paddingTopLeft: [px, py], paddingBottomRight: [px, py], maxZoom: FOLLOW_ZOOM });
        }
      },
      setStep(i) {
        stepIdx = i;
        if (route) draw(null);
      },
      /* Phone compass (degrees from north), or null when there is none. */
      setCompass(deg) {
        compass = deg;
        controls.querySelector('[data-rotate]').hidden = deg === null;
        if (rotateMode && deg !== null) turnTo(deg);
        else schedule();
      },
      setRotateMode(on, byUser = false) {
        rotateMode = on && heading() !== null;
        const b = controls.querySelector('[data-rotate]');
        b.setAttribute('aria-pressed', String(rotateMode));
        b.setAttribute('aria-label', rotateMode ? (labels.northUp || 'North up') : (labels.facingUp || 'Facing up'));
        if (rotateMode) {
          map.dragging.disable(); // turning + dragging together is confusing
          setFollow(true);
          centre();
          turnTo(heading());
        } else {
          map.dragging.enable();
          turnTo(0);
        }
        if (byUser) localStorage.setItem('mapRotate', rotateMode ? 'on' : 'off');
      },
      /* Move the blue dot. Returns the projection onto the current step. */
      update(here) {
        const p = [here.lat, here.lng];
        if (lastHere && metres(lastHere, p) > 3) {
          moveHeading = (Math.atan2((p[1] - lastHere[1]) * Math.cos((p[0] * Math.PI) / 180), p[0] - lastHere[0]) * 180) / Math.PI;
          moveHeading = (moveHeading + 360) % 360;
        }
        if (typeof here.heading === 'number' && !Number.isNaN(here.heading) && (here.speed || 0) > 0.4) moveHeading = here.heading;
        lastHere = p;
        if (!meMarker) {
          accCircle = L.circle(p, { radius: here.acc || 10, color: '#1A73E8', weight: 1, fillColor: '#1A73E8', fillOpacity: 0.12, interactive: false }).addTo(meLayer);
          meMarker = L.marker(p, {
            interactive: false, keyboard: false, zIndexOffset: 1000,
            icon: L.divIcon({ className: 'me-icon', iconSize: [64, 64], iconAnchor: [32, 32], html: '<div class="me-cone" hidden></div><div class="me-dot"></div>' }),
          }).addTo(meLayer);
          if (follow) map.setView(p, FOLLOW_ZOOM);
        } else {
          meMarker.setLatLng(p);
          accCircle.setLatLng(p).setRadius(here.acc || 10);
        }
        if (rotateMode && compass === null && moveHeading !== null) turnTo(moveHeading);
        schedule();
        if (follow) centre();
        if (!route) return null;
        const proj = project(p, paths[stepIdx]);
        if (proj && proj.d < 40) draw(proj);
        return proj;
      },
      heading,
      recentre() { setFollow(true); centre(); },
      refresh() { sizeInner(); },
    };
    return api;
  }

  // Bearing in degrees from a to b ([lat, lng]).
  function bearing(a, b) {
    const toRad = (d) => (d * Math.PI) / 180;
    const y = Math.sin(toRad(b[1] - a[1])) * Math.cos(toRad(b[0]));
    const x = Math.cos(toRad(a[0])) * Math.sin(toRad(b[0])) - Math.sin(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.cos(toRad(b[1] - a[1]));
    return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
  }

  window.LiveMap = { create, project, pathLength, pointAlong, stepPaths, bearing };
})();
