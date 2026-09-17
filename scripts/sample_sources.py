#!/usr/bin/env python3
"""Documented sample relationships — the discovery layer (Phase 1 of the reel maker).

Sources, in order:
  1. WhoSampled, read through the logged-in Dia browser (CDP :9223; the site is Cloudflare-walled
     to plain HTTP). Gives: what was sampled, the element (Multiple Elements / Vocals / Drums /
     Hook-Riff), WHERE it appears in both records ("Sample appears at 3:23"), producers, years.
  2. Wikipedia song articles: "contains a sample of X's 1979 song "Y"" sentences, as a cross-check
     and a fallback when Dia is closed.

Nothing here is treated as truth on its own — sample_reel.py verifies every relationship against
the audio and reports both. Results are cached in ~/.post4me/sources/.

  ~/.yt-dlp-venv/bin/python scripts/sample_sources.py "The Notorious B.I.G." "Hypnotize"
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

CDP = "http://127.0.0.1:9223"
CACHE = os.path.expanduser("~/.post4me/sources")
UA = {"User-Agent": "post4me/0.2 (sample reel maker; contact via github AssiamahS/post4me)"}
TS = re.compile(r"(\d+):(\d\d)")


def slug(*parts):
    return re.sub(r"[^a-z0-9]+", "-", " ".join(parts).lower()).strip("-")


def cached(key, fn):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, key + ".json")
    if os.path.exists(p):
        return json.load(open(p))
    v = fn()
    json.dump(v, open(p, "w"), indent=1, ensure_ascii=False)
    return v


# ---------- Dia / CDP ----------

def cdp_up():
    try:
        urllib.request.urlopen(CDP + "/json/version", timeout=3)
        return True
    except Exception:
        return False


def _browser_call(method, params):
    """One call on the browser-level CDP socket (not a page)."""
    import websocket
    v = json.load(urllib.request.urlopen(CDP + "/json/version", timeout=5))
    ws = websocket.create_connection(v["webSocketDebuggerUrl"], timeout=20, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": method, "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                return m.get("result", {})
    finally:
        ws.close()


class Tab:
    """A page in Dia that never touches the user's own tabs: created in the BACKGROUND, inside a
    separate window (the user complained about tabs popping over what they were reading)."""

    def __init__(self, url):
        import websocket
        r = _browser_call("Target.createTarget", {"url": url, "newWindow": True, "background": True})
        self.id = r["targetId"]
        page_ws = None
        for _ in range(20):
            for t in json.load(urllib.request.urlopen(CDP + "/json", timeout=5)):
                if t.get("id") == self.id:
                    page_ws = t.get("webSocketDebuggerUrl")
            if page_ws:
                break
            time.sleep(0.25)
        self.ws = websocket.create_connection(page_ws, timeout=40, suppress_origin=True)
        self.n = 0
        for _ in range(40):  # wait for the page (Cloudflare interstitial included) to settle
            time.sleep(0.5)
            if self.ev("document.readyState") == "complete" and "moment" not in (self.ev("document.title") or "").lower():
                break
        time.sleep(1.0)

    def ev(self, expr):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": "Runtime.evaluate",
                                 "params": {"expression": expr, "returnByValue": True}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                return m.get("result", {}).get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
            _browser_call("Target.closeTarget", {"targetId": self.id})
        except Exception:
            pass


def _text(tab, expr):
    return tab.ev(expr) or ""


def whosampled_track_url(artist, song):
    tab = Tab("https://www.whosampled.com/search/?q=" + urllib.parse.quote(f"{artist} {song}"))
    try:
        links = json.loads(_text(tab, "JSON.stringify([...document.querySelectorAll('a')].map(a=>[a.getAttribute('href')||'',"
                                       "a.innerText.trim()]).filter(x=>/^\\/[^/]+\\/[^/]+\\/$/.test(x[0])&&x[1]))") or "[]")
    finally:
        tab.close()
    ns = norm(song)
    for href, text in links:
        if href.startswith(("/user/", "/buy/", "/browse/")):
            continue
        if norm(text) == ns or ns in norm(href.replace("-", " ")):
            return "https://www.whosampled.com" + href
    return None


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower().replace("&", "and")).strip()


def whosampled_samples(track_url):
    """[{song, artist, year, element, url}] from the 'Contains samples of' block."""
    tab = Tab(track_url)
    try:
        raw = _text(tab, """(()=>{const h=[...document.querySelectorAll('h3,h2,header,span')]
            .find(e=>/Contains samples? of/i.test(e.innerText||''));if(!h)return '[]';
            const s=h.closest('section')||h.parentElement;
            return JSON.stringify([...s.querySelectorAll('a[href^="/sample/"]')].map(a=>{
              const row=a.closest('div,li,tr')||a.parentElement;
              return [a.getAttribute('href'), a.innerText.trim(), (row.innerText||'').replace(/\\s+/g,' ').trim()]}))})()""")
        rows = json.loads(raw or "[]")
    finally:
        tab.close()
    out, seen = [], set()
    for href, title, row in rows:
        if href in seen or not title:
            continue
        seen.add(href)
        m = re.search(r"(\d{4})\s+(.+)$", row)
        artist = ""
        am = re.search(re.escape(title) + r"\s+(.+?)\s+\d{4}", row)
        if am:
            artist = am.group(1).strip()
        out.append({"song": title, "artist": artist, "year": int(m.group(1)) if m else None,
                    "element": (m.group(2).strip() if m else ""), "url": "https://www.whosampled.com" + href})
    return out


def whosampled_detail(sample_url):
    """The connection page: sample type, where it appears in both records, producers."""
    tab = Tab(sample_url)
    try:
        txt = _text(tab, "document.body.innerText")
        timings = json.loads(_text(tab, "JSON.stringify([...document.querySelectorAll('.timing')].map(e=>[e.className,e.innerText.trim()]))") or "[]")
    finally:
        tab.close()
    d = {"url": sample_url}
    m = re.search(r"(Direct Sample|Interpolation|Replay|Sample)[^\n]*of ([^\n]+)", txt)
    if m:
        d["type"] = m.group(0).strip()
    for cls, val in timings:
        t = TS.search(val)
        if not t:
            continue
        secs = int(t.group(1)) * 60 + int(t.group(2))
        if "dest" in cls:
            d["flip_at"] = secs
        elif "source" in cls:
            d["orig_at"] = secs
    prods = re.findall(r"Producers?:\s*([^\n]+)", txt)
    if prods:
        d["flip_producers"] = [p.strip() for p in prods[0].split(",")]
    if len(prods) > 1:
        d["orig_producers"] = [p.strip() for p in prods[1].split(",")]
    d["throughout"] = "throughout" in txt.split("Sample appears at")[1][:60] if "Sample appears at" in txt else False
    return d


def whosampled(artist, song):
    """Everything WhoSampled documents as sampled IN artist/song, with per-sample detail."""
    def go():
        url = whosampled_track_url(artist, song)
        if not url:
            return {"track_url": None, "samples": []}
        samples = whosampled_samples(url)
        for s in samples:
            try:
                s.update(whosampled_detail(s["url"]))
            except Exception as ex:  # a 500 on one detail page shouldn't kill the lookup
                s["error"] = str(ex)[:120]
        return {"track_url": url, "samples": samples}
    return cached("ws-" + slug(artist, song), go)


# ---------- Wikipedia ----------

def wikipedia(artist, song):
    """Sentences mentioning samples in the song's article (search → first article)."""
    def go():
        q = urllib.parse.urlencode({"action": "query", "list": "search", "srsearch": f"{song} {artist} song",
                                    "format": "json", "srlimit": 3})
        r = json.load(urllib.request.urlopen(urllib.request.Request("https://en.wikipedia.org/w/api.php?" + q, headers=UA), timeout=30))
        hits = r.get("query", {}).get("search", [])
        if not hits:
            return {"title": None, "sentences": []}
        title = hits[0]["title"]
        q = urllib.parse.urlencode({"action": "parse", "page": title, "prop": "wikitext", "format": "json", "redirects": 1})
        d = json.load(urllib.request.urlopen(urllib.request.Request("https://en.wikipedia.org/w/api.php?" + q, headers=UA), timeout=30))
        w = d.get("parse", {}).get("wikitext", {}).get("*", "")
        w = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", w, flags=re.S)
        w = re.sub(r"\[\[([^|\]]*\|)?([^\]]*)\]\]", r"\2", w)
        w = re.sub(r"\{\{[^{}]*\}\}", "", w)
        sents = [s.strip() for s in re.findall(r"[^.\n]*\bsampl\w*[^.\n]*\.", w)]
        return {"title": title, "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                "sentences": sents[:12]}
    return cached("wp-" + slug(artist, song), go)


# ---------- the one call sample_reel uses ----------

def lookup(flip_artist, flip_song, orig_artist, orig_song):
    """Is orig documented as sampled in flip? → {source, url, orig_at, flip_at, element, type,
    producers, quote} or None. WhoSampled first (has timestamps), Wikipedia as confirmation."""
    doc = None
    if cdp_up():
        try:
            ws = whosampled(flip_artist, flip_song)
            for s in ws["samples"]:
                if norm(s["song"]) == norm(orig_song) or norm(orig_song) in norm(s["song"]) or norm(s["song"]) in norm(orig_song):
                    doc = {"source": "whosampled", "url": s["url"], "orig_at": s.get("orig_at"), "flip_at": s.get("flip_at"),
                           "throughout": s.get("throughout"), "element": s.get("element"), "type": s.get("type"),
                           "orig_artist": s.get("artist"), "orig_year": s.get("year"),
                           "flip_producers": s.get("flip_producers"), "orig_producers": s.get("orig_producers")}
                    break
        except Exception as ex:
            print(f"whosampled lookup failed: {ex}", file=sys.stderr)
    try:
        wp = wikipedia(flip_artist, flip_song)
        for sent in wp["sentences"]:
            if norm(orig_song) in norm(sent) or (orig_artist and norm(orig_artist) in norm(sent)):
                if doc:
                    doc["wikipedia"] = {"url": wp["url"], "quote": sent[:240]}
                else:
                    doc = {"source": "wikipedia", "url": wp["url"], "quote": sent[:240]}
                break
    except Exception as ex:
        print(f"wikipedia lookup failed: {ex}", file=sys.stderr)
    return doc


if __name__ == "__main__":
    a, s = sys.argv[1], sys.argv[2]
    out = {"whosampled": whosampled(a, s) if cdp_up() else "dia not running (CDP :9223)", "wikipedia": wikipedia(a, s)}
    print(json.dumps(out, indent=1, ensure_ascii=False))
