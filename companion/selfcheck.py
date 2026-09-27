"""Smoke check: `pixi run check`.

Renders every screen in both languages, exercises Family setup and the setup
link, and plans real routes through the live map service (plus the offline
fallback when that service is down).
"""

from __future__ import annotations

import re
from datetime import datetime
from unittest import mock

from . import create_app, live, services
from .minimetro import DEFAULT_HOURS, ROME, is_running
from .perugia import DEFAULT_CONFIG

P4NOV = "lat=43.112250&lng=12.388800"
STATION = "lat=43.104200&lng=12.376130"


def check_minimetro():
    h = dict(DEFAULT_HOURS)
    assert is_running(h, datetime(2026, 9, 28, 10, 0, tzinfo=ROME))          # Monday
    assert not is_running(h, datetime(2026, 9, 27, 12, 0, tzinfo=ROME))      # closed day
    assert not is_running(h, datetime(2026, 9, 28, 22, 0, tzinfo=ROME))      # night
    assert is_running(h, datetime(2026, 10, 4, 0, 30, tzinfo=ROME))          # late Saturday
    assert not is_running(h, datetime(2026, 10, 4, 8, 0, tzinfo=ROME))       # Sunday before 09:00
    print("minimetro hours: ok")


def setup_form(client, **over):
    f = {"home_name": "Hotel Test", "home_address": "Via dei Priori 1, 06123 Perugia",
         "home_lat": "43.11110", "home_lng": "12.38700", "home_hint": "blue door",
         "contact_name": "Anna", "contact_phone": "+44 7700 900000", "taxi_phone": "+39 075 5004888",
         "fb_lat": "43.11225", "fb_lng": "12.38880", "closed_dates": "2026-09-27"}
    for d in DEFAULT_CONFIG["destinations"]:
        if not d.get("node"):
            p = f"d_{d['id']}_"
            f.update({p + "en": d["name"]["en"], p + "zh": d["name"]["zh"], p + "address": d["address"],
                      p + "lat": str(d["lat"]), p + "lng": str(d["lng"]), p + "hint": ""})
    f.update(over)
    return client.post("/setup", data=f, headers={"Origin": "http://localhost"})


def main() -> None:
    check_minimetro()
    app = create_app({"TESTING": True})
    c = app.test_client()

    r = c.get("/")
    assert r.status_code == 302 and "/welcome" in r.headers["Location"], "first launch must ask language"
    assert c.get("/healthz").get_json() == {"ok": True}, "health check must not redirect"

    dest_ids = ["home"] + [d["id"] for d in DEFAULT_CONFIG["destinations"]]
    for lang in ("en", "zh"):
        c.set_cookie("lang", lang)
        pages = ["/", "/go", "/lost", "/translate", "/show", "/show?purpose=taxi&dest=station", "/setup",
                 "/welcome", "/sw.js"]
        pages += [f"/route/{d}" for d in dest_ids] + [f"/navigate/{d}" for d in dest_ids]
        for p in pages:
            resp = c.get(p)
            assert resp.status_code == 200, (p, resp.status_code)
    print("pages: ok")

    c.set_cookie("lang", "en")
    # No GPS position: never invent one.
    assert c.get("/api/where").get_json() == {"no_location": True}
    assert c.get("/api/route?dest=station").get_json() == {"no_location": True}

    # Family setup is saved on this phone (cookie) and survives a new request.
    r = setup_form(c)
    assert r.status_code == 200 and "Saved" in r.get_data(as_text=True)
    assert "Anna" in c.get("/lost").get_data(as_text=True)
    assert "Via dei Priori 1" in c.get("/show").get_data(as_text=True)
    bad = setup_form(c, home_lat="north")
    assert "check the coordinates" in bad.get_data(as_text=True)
    # A form posted from another site (or with no origin at all) is refused.
    assert c.post("/setup", data={}, headers={"Origin": "https://evil.example"}).status_code == 400
    assert c.post("/setup", data={}).status_code == 400
    # Brave on iOS: saving must work even if the setup page's cookie never came back.
    fresh = app.test_client()
    fresh.set_cookie("lang", "en")
    assert "Saved" in setup_form(fresh).get_data(as_text=True)
    # Some browsers send only a Referer.
    r = fresh.post("/setup", data={}, headers={"Referer": "http://localhost/setup"})
    assert r.status_code == 200 and "check the coordinates" in r.get_data(as_text=True)

    # The setup link copies everything to another phone.
    link = re.search(r'id="setup-link" value="([^"]+)"', c.get("/setup").get_data(as_text=True)).group(1)
    other = app.test_client()
    r = other.get(link.replace("http://localhost", ""))
    assert r.status_code == 302
    assert "Anna" in other.get("/lost").get_data(as_text=True)
    assert other.get("/s/garbage").status_code == 400
    print("family setup + setup link: ok")

    w = c.get(f"/api/where?{P4NOV}").get_json()
    print(f"where: near={w['near']} hotel={w['home_m']} m")

    for dest, start in [("station", P4NOV), ("home", STATION), ("pincetto", P4NOV), ("pharmacy", P4NOV)]:
        data = c.get(f"/api/route?dest={dest}&{start}").get_json()
        assert not data["taxi"], data
        for mode in ("easiest", "safest", "fastest"):
            p = data[mode]
            assert p["steps"][-1]["kind"] == "arrive"
            assert all(s["text"] for s in p["steps"])
            assert set(p["crossings"]) == {"signals", "zebra", "crossing", "unmarked"}
        e, f, sf = data["easiest"], data["fastest"], data["safest"]
        safest_line = (f"{'':9} safest {sf['minutes']:>2} min, crossings {sf['crossings']}, main streets {sf['main_pct']}%")
        print(f"{dest:<9} [{data['engine']}] easiest {e['minutes']:>2} min, {e['stairs']:>3} steps, "
              f"climb {e['climb']:>3} m, {e['uses']} | fastest {f['minutes']:>2} min, {f['stairs']:>3} steps"
              f"{' (same)' if data['same'] else ''}")
        print(safest_line)
    assert c.get(f"/api/route?dest=hospital&{P4NOV}").get_json()["taxi"]

    # Search box: names, addresses, coordinates and Google Maps links.
    from .search import search
    from .services import resolve_maps_link
    assert search("43.11040, 12.38830")[0]["lat"] == 43.1104
    link = "https://www.google.com/maps/place/Fontana+Maggiore/@43.1121,12.3886,17z/data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d43.1122216!4d12.3887921"
    hit = search(link)[0]
    assert hit["name"] == "Fontana Maggiore" and abs(hit["lat"] - 43.1122216) < 1e-6, hit
    assert resolve_maps_link("https://evil.example/x") is None
    # A real shared short link (from a public GitHub page): uses the exact pin, not the map centre.
    shared = search("Copenhagen Coffee Lab\nsomewhere\nhttps://maps.app.goo.gl/Crj1o13NznuE5fZn8")
    assert shared and shared[0]["name"] == "Fyllingsdalen Tunnel" and abs(shared[0]["lng"] - 5.2938481) < 1e-6, shared
    # Official street numbers: doors OpenStreetMap doesn't have.
    door = search("Via dei Priori 20")[0]
    assert door["name"] == "Via dei Priori 20" and door["precise"], door
    hotel = search("Casa Valentina 3, 27 Via Cortonese piano 6 interno 607, Perugia, 06124")[0]
    assert hotel["name"] == "Via Cortonese 27" and abs(hotel["lat"] - 43.106379) < 1e-5, hotel
    geo = c.get("/api/geocode?q=Via dei Priori 20").get_json()["results"]
    assert geo[0]["precise"] and geo[0]["name"].startswith("Via dei Priori 20"), geo
    names = [r["name"] for r in c.get("/api/search?q=farmacia lemmi").get_json()["results"]]
    assert any("Lemmi" in n for n in names), names
    addr = c.get("/api/search?q=Corso Vannucci 57, Perugia").get_json()["results"]
    assert addr and all(42.9 < r["lat"] < 43.3 for r in addr), addr
    place = "to_lat=43.110430&to_lng=12.388300&to_name=Farmacia+Lemmi&to_addr=Corso+Vannucci"
    for p in (f"/route/place?{place}", f"/navigate/place?{place}", f"/show?dest=place&{place}"):
        resp = c.get(p)
        assert resp.status_code == 200 and "Farmacia Lemmi" in resp.get_data(as_text=True), p
    assert c.get("/route/place?to_lat=x").status_code == 404
    data = c.get(f"/api/route?dest=place&{place}&{P4NOV}").get_json()
    assert data["easiest"]["steps"][-1]["text"].startswith("Farmacia Lemmi"), data["easiest"]["steps"][-1]
    print("search + one-off places: ok", names[:3])

    one = c.get(f"/api/route?dest=station&mode=easiest&no_metro=1&{P4NOV}").get_json()
    assert "fastest" not in one and "metro" not in one["easiest"]["uses"]
    print("no-metro reroute:", one["easiest"]["minutes"], "min")

    # Map service down: simpler offline directions instead of an error.
    with mock.patch.object(live.svc, "walk_route", side_effect=services.ServiceError("down")):
        data = c.get(f"/api/route?dest=station&{P4NOV}").get_json()
    assert data["engine"] == "offline" and data["easiest"]["steps"][-1]["kind"] == "arrive"
    print("offline fallback: ok")

    # Crossing directions are spoken where the route crosses a road.
    walk = c.get(f"/api/route?dest=station&mode=safest&{STATION.replace('43.104200', '43.104300').replace('12.376130', '12.389600')}").get_json()
    texts = " ".join(s["text"] for s in walk["safest"]["steps"])
    assert "crossing" in texts or "traffic lights" in texts, texts[:300]

    # The family can make "Safest" the recommended route.
    setup_form(c, default_mode="safest")
    assert c.get(f"/api/route?dest=pharmacy&{P4NOV}").get_json()["default_mode"] == "safest"
    assert 'data-mode="safest"' in c.get("/navigate/home").get_data(as_text=True)
    print("safest route + crossings: ok")

    # Test mode uses the test position without GPS.
    setup_form(c, pretend="on")
    assert c.get("/api/where").get_json()["test"] is True

    t = c.post("/api/translate", json={"text": "Where is the Minimetrò?", "from": "en"}).get_json()
    assert t == {"it": "Dov'è il Minimetrò?", "source": "phrasebook"}, t
    t = c.post("/api/translate", json={"text": "迷你地铁在哪里？", "from": "zh"}).get_json()
    assert t["it"] == "Dov'è il Minimetrò?", t
    print("translate phrasebook: ok")
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
