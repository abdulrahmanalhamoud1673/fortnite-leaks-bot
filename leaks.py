#!/usr/bin/env python3
"""Leaks & news poster for the أبود قنّاص Discord — Fortnite, Minecraft and GTA.

Runs on GitHub Actions about every 5 minutes. Each game posts to its own channel through
its own webhook secret; a game whose secret is missing is simply skipped.

  fortnite   DISCORD_WEBHOOK            new game versions, items datamined into the game files and
                                        what is new in the item shop (fortnite-api.com), drawn as one
                                        zoomable picture sorted into sections (cards.py) plus Epic's clips
  minecraft  DISCORD_WEBHOOK_MINECRAFT  every snapshot/pre-release/release with Mojang's own patch-note picture,
                                        Mojang's official news, and Arabic Minecraft news with pictures
  gta        DISCORD_WEBHOOK_GTA        GTA 5 / GTA 6 news from Arabic gaming sites, each with its picture

With GEMINI_API_KEY set, every news post is rewritten as a short, clear Arabic headline + summary
(arabic.py). If Gemini is busy, the item waits for the next run (up to MAX_HOLDS times) before the
original text is posted. Big news pings the game's opt-in 🔔 role, so only members who chose it hear
about it. Everything already posted is remembered in state.json, so nothing is sent twice.
"""
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import arabic
import cards

STATE = os.environ.get("LEAKS_STATE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
PREVIEW = os.environ.get("PREVIEW_DIR")          # with DRY_RUN, pictures are written here instead of sent
AVATAR = ("https://yt3.googleusercontent.com/64sSctZiJSIBBsfI_R_tWo2tV3bYF2LP0xr5Mc6SPFurxKFMqe1m02dQ2z7MgvhIxX8pybPs"
          "=s256-c-k-c0x00ffffff-no-rj")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128 Safari/537.36 abod-leaks-bot/1.5"}
MAX_HOLDS = 3                                   # runs (≈5 min apart) to wait for Gemini before posting the original
ROLE_FN, ROLE_MC, ROLE_GTA = "1547928400616886273", "1547929115066241095", "1547929149027385477"   # 🔔 opt-in roles
_CACHE = {}


# ---------------------------------------------------------------- helpers
def http_get(url):
    if url not in _CACHE:                      # the GTA and Minecraft jobs read the same Arabic feeds
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            _CACHE[url] = r.read()
    return _CACHE[url]


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return {}
    if "posted" in state:                      # v1 file held only the Fortnite state
        state = {"fortnite": state}
    return state


def save_state(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def send(webhook, body, content_type):
    url = webhook + ("&" if "?" in webhook else "?") + "wait=true"
    for _ in range(6):
        req = urllib.request.Request(url, data=body, headers={**UA, "Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                r.read()
            time.sleep(1.2)
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 2)) + 0.5)
                continue
            raise
    raise RuntimeError("Discord kept rate-limiting the webhook")


def with_ping(payload, role):
    """Mention an opt-in 🔔 role: only the members who picked that game get the notification."""
    if not role:
        return payload
    return {**payload, "content": (f"<@&{role}> " + payload.get("content", "")).strip(),
            "allowed_mentions": {"parse": [], "roles": [role]}}


def post(webhook, name, payload):
    payload = {"username": name, "avatar_url": AVATAR, "allowed_mentions": {"parse": []}, **payload}
    if DRY_RUN:
        print("   DRY:", json.dumps(payload, ensure_ascii=False)[:300])
        return
    send(webhook, json.dumps(payload).encode("utf-8"), "application/json")


def post_file(webhook, name, payload, filename, data):
    """A message with one attached picture, which an embed can show as attachment://<filename>."""
    payload = {"username": name, "avatar_url": AVATAR, "allowed_mentions": {"parse": []}, **payload}
    if DRY_RUN:
        if PREVIEW:
            with open(os.path.join(PREVIEW, filename), "wb") as f:
                f.write(data)
        print(f"   DRY: {filename} ({len(data) // 1024} KB) +", json.dumps(payload, ensure_ascii=False)[:300])
        return
    boundary = "abodleaks" + os.urandom(8).hex()
    body = b"".join([
        (f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
         "Content-Type: application/json\r\n\r\n").encode(), json.dumps(payload).encode("utf-8"),
        (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'
         "Content-Type: image/jpeg\r\n\r\n").encode(), data, f"\r\n--{boundary}--\r\n".encode()])
    send(webhook, body, f"multipart/form-data; boundary={boundary}")


def hold(retry, key, written):
    """True = keep this item for the next run: Gemini failed and there are holds left."""
    if written or not arabic.enabled() or retry.get(key, 0) >= MAX_HOLDS:
        retry.pop(key, None)
        return False
    retry[key] = retry.get(key, 0) + 1
    return True


def norm(title):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", title.lower())).strip()[:60]


SAME = {"بلايستيشن": "playstation", "ps5": "playstation", "روكستار": "rockstar", "تريلر": "trailer",
        "الدعائي": "trailer", "دعائي": "trailer"}
STOP = {"gta", "لعبة", "لعب", "على", "عن", "من", "مع", "بعد", "قبل", "هل", "the", "and", "for", "with"}


def stems(title):
    """Rough Arabic stems (no ال/و/ب prefixes, no ات/ون/ة endings), so two sites' headlines can be compared."""
    out = set()
    for w in norm(title).split():
        w = SAME.get(w, w)
        if len(w) > 4:
            w = re.sub(r"^(وال|بال|فال|كال|لل|ال|و|ب|ف)", "", w)
        if len(w) > 4:
            w = re.sub(r"(ات|ون|ين|ية|ة|ه)$", "", w)
        w = SAME.get(w, w)
        if len(w) >= 3 and w not in STOP:
            out.add(w)
    return out


def similar(sa, sb):
    """Same story? sa/sb are stems() of two headlines — sites rarely word the same news alike."""
    shared = len(sa & sb)
    return shared >= 4 or (shared >= 3 and shared / max(1, min(len(sa), len(sb))) >= 0.5)


def plain(text):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    text = re.sub(r"(The post|ظهرت المقالة|The article).*$", "", text, flags=re.S)
    return re.sub(r"\s+", " ", text).strip()


def bullets(points):
    return "\n".join("• " + p for p in points)


MEDIA = "{http://search.yahoo.com/mrss/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"


def parse_rss(raw, source=""):
    items = []
    for it in ET.fromstring(raw).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip() or source
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            pub = datetime.now(timezone.utc)
        body = it.findtext(CONTENT) or it.findtext("description") or ""
        image = None
        enc = it.find("enclosure")
        if enc is not None and (enc.get("type") or "").startswith("image"):
            image = enc.get("url")
        for tag in (MEDIA + "content", MEDIA + "thumbnail"):
            if not image and it.find(tag) is not None:
                image = it.find(tag).get("url")
        if not image:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)', body)
            image = html.unescape(m.group(1)) if m else None
        items.append({"title": title, "link": (it.findtext("link") or "").strip(), "source": src, "pub": pub,
                      "image": image, "summary": plain(it.findtext("description") or body)})
    return items


def page_meta(url):
    """The article's own share picture and description (og: tags), for feeds that carry little."""
    try:
        page = http_get(url)[:600_000].decode("utf-8", "ignore")
    except Exception:
        return None, None

    def og(prop):
        m = (re.search(rf'<meta[^>]+property=["\']og:{prop}["\'][^>]+content=["\']([^"\']+)', page)
             or re.search(rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:{prop}', page))
        return html.unescape(m.group(1)).strip() if m else None
    return og("image"), og("description")


# ---------------------------------------------------------------- Arabic gaming news (GTA + Minecraft)
AR_FEEDS = [("https://me.ign.com/ar/feed.xml", "IGN الشرق الأوسط"),        # سعودي جيمر answers bots with 403
            ("https://www.true-gaming.net/home/feed/", "ترو جيمنج")]
IMPORTANT = re.compile(r"رسمي|تريلر|العرض الدعائي|عرض دعائي|موعد|تأجيل|سعر|تسريب|تكشف|تعلن|الإعلان|"
                       r"trailer|release date|leak|official|delay", re.I)
FEEDS_VERSION = 2                              # 1 = Google News headlines without pictures


def news_embed(it, label, color):
    """(embed, written, important) — written is False when Gemini couldn't rewrite it."""
    image, desc = it.get("image"), it.get("summary") or ""
    if not image or len(desc) < 80:            # thin feed entry: read the article's own og: tags
        og_image, og_desc = page_meta(it["link"])
        image = image or og_image
        if og_desc and len(og_desc) > len(desc):
            desc = og_desc
    ai = arabic.rewrite("خبر ألعاب", it["title"], desc, it["source"])
    if ai:
        important = ai["important"]
        title = f"{ai['emoji']} {ai['title']}".strip()
        body = [ai["summary"]] + ([bullets(ai["points"])] if ai["points"] else [])
    else:
        important = bool(IMPORTANT.search(it["title"]))
        title = ("🔥 " if important else "") + it["title"]
        summary = desc if len(desc) <= 240 else desc[:240].rsplit(" ", 1)[0] + "…"
        body = [summary] if summary and not summary.startswith(it["title"][:30]) else []
    body.append(f"📰 المصدر: {it['source']}" + ("  •  🔥 **خبر مهم**" if important else ""))
    e = {"title": title[:250], "url": it["link"], "description": "\n\n".join(body)[:1000],
         "color": 0xEF4444 if important else color, "footer": {"text": label}, "timestamp": it["pub"].isoformat()}
    if image:
        e["image"] = {"url": image}
    return e, ai is not None, important


def run_feeds(st, webhook, name, keep, noise, label, color, intro=None, per_run=3, per_day=10, max_age_h=36,
              role=None):
    """Fresh, relevant articles from the Arabic gaming sites, each posted with its picture.
    Important ones ping `role`."""
    items = []
    for url, source in AR_FEEDS:
        try:
            items += parse_rss(http_get(url), source)
        except Exception as e:                  # one site down must not stop the others
            print(f"   {label}: {source} failed: {e!r}")
    seen = set(st.get("seen", []))
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"], st["count"] = today, 0
    brand_new, switching = not seen, st.get("src") != FEEDS_VERSION
    posted = [p for p in st.get("titles", []) if isinstance(p, list) and len(p) == 2]
    cutoff = (now - timedelta(hours=48)).isoformat()
    covered = [t for d, t in posted if d >= cutoff]           # stories posted in the last two days
    if switching:                               # plus whatever the old headline source already covered
        covered += [s for s in seen if not s.startswith("http")]
    known = [stems(t) for t in covered]

    relevant = sorted((it for it in items
                       if it["link"] not in seen and norm(it["title"]) not in seen and keep.search(it["title"])
                       and not (noise and noise.search(it["title"] + " " + it["source"]))
                       and now - it["pub"] <= timedelta(hours=max_age_h)), key=lambda x: x["pub"], reverse=True)
    batch = []
    for it in relevant:                         # the same story from several sites → the newest one only
        s = stems(it["title"])
        if not any(similar(s, k) for k in known):
            batch.append(it)
            known.append(s)
    room = max(0, per_day - st.get("count", 0))
    chosen = batch[: (2 if brand_new else 1 if switching else min(per_run, room))]
    if brand_new and intro:
        post(webhook, name, {"content": intro})
    retry, sent, held = st.get("retry", {}), [], set()
    for it in reversed(chosen):
        embed, written, important = news_embed(it, label, color)
        if hold(retry, it["link"], written):
            held.add(it["link"])
            continue
        loud = important and not (brand_new or switching)
        post(webhook, name, with_ping({"embeds": [embed]}, role if loud else None))
        sent.append(it)
    st["retry"] = {k: n for k, n in retry.items() if k in held}
    st["count"] = st.get("count", 0) + len(sent)
    st["seen"] = (list(seen) + [x for it in items if it["link"] not in held
                                for x in (it["link"], norm(it["title"]))])[-2000:]
    st["titles"] = (posted + [[it["pub"].astimezone(timezone.utc).isoformat(), it["title"]] for it in sent])[-150:]
    st["src"] = FEEDS_VERSION
    print(f"   {label}: {len(items)} articles, {len(batch)} new and relevant, posted {len(sent)}"
          + (f", {len(held)} held for the Arabic rewrite" if held else ""))


# ---------------------------------------------------------------- Fortnite
FN_API = "https://fortnite-api.com/v2/cosmetics/new?language=ar"
FN_AES = "https://fortnite-api.com/v2/aes"                       # its build changes the moment a version ships
FN_SHOP = "https://fortnite-api.com/v2/shop?language=ar"
FN_ITEM = "https://fortnite-api.com/v2/cosmetics/br/"
FN_NAME = "تسريبات أبود 🔥"
FOOTER = "تسريبات أبود  •  كل جديد فورتنايت أول بأول  •  youtube.com/@Ab_sn6"
SHOP_DESIGN = 2                                # bump to re-post today's shop once in a new look
MAX_TILES = 35
RARITY = {
    "mythic": (9, 0xF4C430), "legendary": (8, 0xF39C12), "icon": (8, 0x2EC4DC),
    "marvel": (8, 0xC0392B), "dc": (8, 0x3A6BC7), "starwars": (8, 0x3B3B3B),
    "gaminglegends": (8, 0x6C3FC7), "shadow": (7, 0x505050), "slurp": (7, 0x13C8C8),
    "frozen": (7, 0x9FD8F0), "lava": (7, 0xD1491A), "dark": (7, 0xB22CB2),
    "epic": (6, 0x9B59B6), "rare": (4, 0x3498DB), "uncommon": (2, 0x2ECC71), "common": (1, 0x95A5A6),
}
TYPE_WEIGHT = {"outfit": 5, "backpack": 3, "pickaxe": 3, "glider": 3, "emote": 3, "wrap": 2}
SECTIONS = [("bundle", "الباقات", "📦"), ("outfit", "السكنات", "🧍"), ("emote", "الرقصات", "💃"),
            ("pickaxe", "الفؤوس", "⛏️"), ("backpack", "زينة الظهر", "🎒"), ("glider", "المظلات", "🪂"),
            ("wrap", "الأغلفة", "🎨"), ("other", "أشياء ثانية", "✨")]
AR_DAYS = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
AR_MONTHS = ["كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران",
             "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول"]


def ar_date(iso):
    d = datetime.strptime(iso[:10], "%Y-%m-%d")
    return f"{AR_DAYS[d.weekday()]} {d.day} {AR_MONTHS[d.month - 1]}"


def fn_version(build):
    m = re.search(r"Release-(\d+\.\d+)", build or "")
    return m.group(1) if m else None


def item_kind(item):
    return ((item.get("type") or {}).get("value") or "").lower()


def section_key(kind):
    return kind if kind in {"outfit", "emote", "pickaxe", "backpack", "glider", "wrap"} else "other"


def fn_weight(item):
    rarity = ((item.get("rarity") or {}).get("value") or "").lower()
    return RARITY.get(rarity, (0, 0))[0] * 10 + TYPE_WEIGHT.get(item_kind(item), 1)


def group(things, key_of, tiles_of):
    """Split into the SECTIONS order. Returns ([(section title, tiles)], summary line, how many drawn)."""
    buckets = {}
    for x in things:
        buckets.setdefault(key_of(x), []).append(x)
    sections, summary, room = [], [], MAX_TILES
    for key, title, emoji in SECTIONS:
        xs = buckets.get(key) or []
        if not xs:
            continue
        summary.append(f"{emoji} {title}: {len(xs)}")
        if room > 0:
            sections.append((title, tiles_of(xs[:room])))
            room -= len(xs[:room])
    return sections, " • ".join(summary), MAX_TILES - room


def card_embed(title, lines, filename):
    return {"title": title, "description": "\n".join(lines), "color": 0x8B5CF6,
            "image": {"url": f"attachment://{filename}"},
            "footer": {"text": "تسريبات أبود • اضغط على الصورة وكبّرها 🔍"},
            "timestamp": datetime.now(timezone.utc).isoformat()}


def showcase_video(item, lookup):
    """Epic's own showcase clip on YouTube: Discord plays it inside the channel."""
    vid = item.get("showcaseVideo")
    if not vid and lookup and item.get("id"):
        try:
            vid = json.loads(http_get(FN_ITEM + item["id"]))["data"].get("showcaseVideo")
        except Exception:
            vid = None
    return vid


def post_videos(webhook, pairs, lookup, limit=4, max_lookups=12):
    """pairs: (item, price or None). Emotes first — a picture can't show a dance — then outfits."""
    order = {"emote": 0, "outfit": 1}
    done, ids, shown = 0, set(), 0
    for item, price in sorted(pairs, key=lambda p: order.get(item_kind(p[0]), 2)):
        if item.get("id") in ids or done >= max_lookups or shown >= limit:
            continue
        ids.add(item.get("id"))
        done += 1
        vid = showcase_video(item, lookup)
        if not vid:
            continue
        name, tail = item.get("name") or "؟", (f" • {price:,} V-Bucks" if price else "")
        caption = {"emote": f"💃 **{name}** — شوفوا الرقصة ▶️{tail}",
                   "outfit": f"🧍 **{name}** — شوفوا السكن من كل الجهات ▶️{tail}"}.get(
            item_kind(item), f"✨ **{name}** ▶️{tail}")
        if shown == 0:
            caption = "🎬 **شوفوها بالحركة:**\n" + caption
        post(webhook, FN_NAME, {"content": f"{caption}\nhttps://www.youtube.com/watch?v={vid}"})
        shown += 1
    return shown


def fn_embed(item):
    rarity = item.get("rarity") or {}
    series = item.get("series") or {}
    kind = (item.get("type") or {}).get("displayValue") or ""
    desc = " • ".join(x for x in (kind, series.get("value") or rarity.get("displayValue") or "") if x)
    set_text = (item.get("set") or {}).get("text")
    if set_text:
        desc += "\n" + set_text
    images = item.get("images") or {}
    e = {"title": (item.get("name") or item.get("id") or "؟")[:250], "description": desc[:400],
         "color": RARITY.get((rarity.get("value") or "").lower(), (0, 0x8B5CF6))[1]}
    icon = images.get("icon") or images.get("smallIcon")
    if icon:
        e["thumbnail"] = {"url": icon}
    return e


def leak_tiles(items):
    tiles = []
    for it in items:
        series = (it.get("series") or {}).get("colors") or []
        base = RARITY.get(((it.get("rarity") or {}).get("value") or "").lower(), (0, 0x8B5CF6))[1]
        base = ((base >> 16) & 255, (base >> 8) & 255, base & 255)
        colors = ((cards.hex_rgb(series[0]), cards.hex_rgb(series[-1])) if len(series) >= 2
                  else (cards.shade(base, 1.25), cards.shade(base, 0.45)))
        images = it.get("images") or {}
        line = (it.get("series") or {}).get("value") or (it.get("rarity") or {}).get("displayValue") or ""
        tiles.append({"name": it.get("name") or it.get("id") or "؟", "line": line, "colors": colors,
                      "image": images.get("featured") or images.get("icon") or images.get("smallIcon")})
    return tiles


def run_fn_version(st, webhook):
    """Announce a new game version as soon as its build appears."""
    ver = fn_version(json.loads(http_get(FN_AES))["data"].get("build"))
    if not ver:
        return
    prev, st["version"] = st.get("version"), ver
    if prev and prev != ver:
        post(webhook, FN_NAME, with_ping({"embeds": [{
            "title": f"🆕 تحديث فورتنايت {ver} نزل!",
            "description": "حدّثوا اللعبة 🎮\nالسكنات والرقصات الجديدة اللي انضافت بالتحديث رح تنزل هون أول ما تبين 👀",
            "color": 0x8B5CF6, "footer": {"text": f"Fortnite v{ver}"},
            "timestamp": datetime.now(timezone.utc).isoformat()}]}, ROLE_FN))
    print(f"   fortnite version: {ver}" + (f" (was {prev} — announced)" if prev and prev != ver else ""))


def run_fn_leaks(st, webhook):
    """Items datamined into the game files with each update: skins, emotes, pickaxes…"""
    data = json.loads(http_get(FN_API))["data"]
    items = (data.get("items") or {}).get("br") or []
    build = fn_version(data.get("build")) or (data.get("build") or "؟")
    posted = set(st.get("posted", []))
    first = not posted
    fresh = sorted((x for x in items if x.get("id") not in posted), key=fn_weight, reverse=True)
    if not fresh:
        print(f"   fortnite leaks: nothing new (build {build})")
        return
    pool = fresh[:6] if first else fresh
    sections, summary, drawn = group(pool, lambda it: section_key(item_kind(it)), leak_tiles)
    star = next((x for x in pool if item_kind(x) == "outfit"), pool[0])
    if first:
        title = "✅ بوت التسريبات اشتغل!"
        lines = [f"🧩 التحديث {build}", summary,
                 "من هلأ، كل ما ينزل تحديث لفورتنايت، رح توصلكم هون السكنات والرقصات والأدوات الجديدة 👀"]
    else:
        title = f"🔥 تسريبات جديدة بفورتنايت — التحديث {build}"
        lines = [f"🆕 انضاف {len(fresh)} عنصر جديد لملفات اللعبة", summary,
                 f"⭐ أبرز شي: **{star.get('name') or '؟'}**"]
        if len(fresh) > drawn:
            lines.append(f"➕ وكمان {len(fresh) - drawn} عنصر ثاني… رح يبينوا بالمتجر مع الوقت 👀")
    role = None if first else ROLE_FN
    try:
        jpg = cards.draw_card("تسريبات فورتنايت", f"التحديث {build} • {len(fresh)} عنصر جديد بملفات اللعبة",
                              sections, FOOTER, AVATAR)
        post_file(webhook, FN_NAME, with_ping({"embeds": [card_embed(title, lines, "leaks.jpg")]}, role),
                  "leaks.jpg", jpg)
    except Exception as e:                      # no Chrome / a broken image: fall back to plain embeds
        print(f"   leaks card failed ({e!r}) — sending embeds instead")
        post(webhook, FN_NAME, with_ping({"content": f"**{title}**\n" + "\n".join(lines)}, role))
        for i in range(0, min(30, len(pool)), 10):
            post(webhook, FN_NAME, {"embeds": [fn_embed(x) for x in pool[i:i + 10]]})
    videos = post_videos(webhook, [(x, None) for x in pool[:MAX_TILES]], lookup=False)
    st.update({"build": data.get("build"), "date": data.get("date"),
               "posted": sorted(posted | {x.get("id") for x in items if x.get("id")})[-3000:]})
    print(f"   fortnite leaks: {len(fresh)} new from build {build}, card with {drawn}, {videos} videos")


def shop_weight(entry):
    kinds = [item_kind(it) for it in entry["brItems"]]
    return (1 if entry.get("bundle") else 0, max(TYPE_WEIGHT.get(k, 1) for k in kinds), entry.get("finalPrice") or 0)


def shop_main(entry):
    return max(entry["brItems"], key=lambda it: TYPE_WEIGHT.get(item_kind(it), 1))


def shop_name(entry):
    return (entry.get("bundle") or {}).get("name") or shop_main(entry).get("name") or "؟"


def shop_embed(entry):
    items = entry["brItems"]
    bundle = entry.get("bundle") or {}
    main = shop_main(entry)
    price, regular = entry.get("finalPrice") or 0, entry.get("regularPrice") or 0
    lines = [f"💰 **{price:,}** V-Bucks" + (f"  (بدل ~~{regular:,}~~) 🔻" if regular > price else "")]
    kind = f"📦 باقة فيها {len(items)} عناصر" if bundle else (main.get("type") or {}).get("displayValue") or ""
    series = (main.get("series") or {}).get("value") or (main.get("rarity") or {}).get("displayValue") or ""
    lines.append(" • ".join(x for x in (kind, series) if x))
    if entry.get("outDate"):
        lines.append(f"⏳ موجود لحد {ar_date(entry['outDate'])}")
    e = {"title": shop_name(entry)[:250], "description": "\n".join(lines)[:400],
         "color": int(((entry.get("colors") or {}).get("color1") or "8b5cf6")[:6], 16)}
    renders = (entry.get("newDisplayAsset") or {}).get("renderImages") or []
    image = (renders[0].get("image") if renders else None) or bundle.get("image") or (main.get("images") or {}).get("icon")
    if image:
        e["thumbnail"] = {"url": image}
    return e


def shop_tiles(entries):
    tiles = []
    for e in entries:
        bundle, main, c = e.get("bundle") or {}, shop_main(e), e.get("colors") or {}
        renders = (e.get("newDisplayAsset") or {}).get("renderImages") or []
        badge = f"{len(e['brItems'])} عناصر" if bundle else (main.get("type") or {}).get("displayValue")
        tiles.append({"name": shop_name(e), "price": e.get("finalPrice"), "regular": e.get("regularPrice"),
                      "badge": badge,
                      "colors": (cards.hex_rgb(c.get("color1")), cards.hex_rgb(c.get("color3") or c.get("color2"))),
                      "image": (renders[0].get("image") if renders else None) or bundle.get("image")
                      or (main.get("images") or {}).get("icon")})
    return tiles


def run_fn_shop(st, webhook):
    """What is new in the item shop — after each daily reset and whenever items get added."""
    if st.get("design") != SHOP_DESIGN:        # a new look: show today's shop once more in it
        st.clear()
    data = json.loads(http_get(FN_SHOP))["data"]
    if data.get("hash") and data.get("hash") == st.get("hash"):
        print("   fortnite shop: unchanged")
        return
    date = (data.get("date") or "")[:10]
    entries = [e for e in data.get("entries") or [] if e.get("brItems")]   # skins, emotes… not music tracks
    known = set(st.get("offers", []))
    if known:
        fresh = [e for e in entries if e.get("offerId") not in known]
    else:                                                                  # first run: what came with today's reset
        fresh = [e for e in entries if (e.get("inDate") or "")[:10] == date]
    new_day = bool(known) and st.get("date") != date
    st.update({"design": SHOP_DESIGN, "hash": data.get("hash"), "date": date,
               "offers": [e.get("offerId") for e in entries if e.get("offerId")]})
    names, unique = set(), []                  # one item can sit in several offers → show it once
    for e in sorted(fresh, key=shop_weight, reverse=True):
        name = shop_name(e)
        if name not in names:
            names.add(name)
            unique.append(e)
    if not unique:
        print("   fortnite shop: changed, nothing new")
        return
    sections, summary, drawn = group(
        unique, lambda e: "bundle" if e.get("bundle") else section_key(item_kind(shop_main(e))), shop_tiles)
    if not known:
        title = "🛒 متجر فورتنايت اليوم"
        extra = ["من هلأ، كل ما يتجدد المتجر (الساعة 3 الفجر) رح ينزل هون الجديد فيه أول بأول"]
    elif new_day:
        title, extra = "🛒 متجر فورتنايت الجديد نزل!", []
    else:
        title, extra = "🛒 انضافت أشياء جديدة للمتجر!", []
    lines = [f"📅 {ar_date(date)} • 🆕 {len(unique)} عنصر جديد", summary]
    deals = [e for e in unique if (e.get("regularPrice") or 0) > (e.get("finalPrice") or 0) > 0]
    if deals:
        d = max(deals, key=lambda e: (e["regularPrice"] - e["finalPrice"]) / e["regularPrice"])
        lines.append(f"💥 أقوى خصم: **{shop_name(d)}** بـ {d['finalPrice']:,} بدل ~~{d['regularPrice']:,}~~ V-Bucks")
    lines += extra
    if len(unique) > drawn:
        lines.append(f"➕ وكمان {len(unique) - drawn} عنصر — شوفوهم باللعبة 🎮")
    role = ROLE_FN if new_day else None         # one ping a day, for the fresh shop — not for every top-up
    try:
        jpg = cards.draw_card("متجر فورتنايت", f"{ar_date(date)} • {len(unique)} عنصر جديد", sections, FOOTER, AVATAR)
        post_file(webhook, FN_NAME, with_ping({"embeds": [card_embed(title, lines, "shop.jpg")]}, role),
                  "shop.jpg", jpg)
    except Exception as e:                      # no Chrome / a broken image: fall back to plain embeds
        print(f"   shop card failed ({e!r}) — sending embeds instead")
        post(webhook, FN_NAME, with_ping({"content": f"**{title}**\n" + "\n".join(lines)}, role))
        for i in range(0, min(20, len(unique)), 10):
            post(webhook, FN_NAME, {"embeds": [shop_embed(e) for e in unique[i:i + 10]]})
    pairs = [(it, e.get("finalPrice") if len(e["brItems"]) == 1 else None)
             for e in unique[:MAX_TILES] for it in e["brItems"]]
    videos = post_videos(webhook, pairs, lookup=True)
    print(f"   fortnite shop: {len(unique)} new offers, card with {drawn}, {videos} videos")


def run_fortnite(st, webhook):
    for label, fn, sub in (("version", run_fn_version, st), ("leaks", run_fn_leaks, st),
                           ("shop", run_fn_shop, st.setdefault("shop", {}))):
        try:
            fn(sub, webhook)
        except Exception as e:                  # one broken part must not stop the others
            print(f"   fortnite {label} failed: {e!r}")


# ---------------------------------------------------------------- Minecraft
MC_NAME = "تسريبات أبود ⛏️"
MOJANG = "https://launchercontent.mojang.com"
MC_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MC_AR_KEEP = re.compile(r"Minecraft|ماين\s*كرافت|ماينكرافت|Mojang|موجانج", re.I)


def mc_versions():
    """Mojang's patch notes (with their picture and summary); the bare version list if those fail."""
    try:
        notes = json.loads(http_get(MOJANG + "/v2/javaPatchNotes.json"))["entries"]
        notes.sort(key=lambda e: e.get("date") or "", reverse=True)
        return [{"id": e.get("version"), "type": e.get("type"), "time": e.get("date"), "text": e.get("shortText"),
                 "path": e.get("contentPath"),
                 "image": MOJANG + e["image"]["url"] if (e.get("image") or {}).get("url") else None} for e in notes]
    except Exception as e:
        print(f"   minecraft patch notes failed ({e!r}) — using the version list")
        return [{"id": v["id"], "type": v["type"], "time": v.get("releaseTime")}
                for v in json.loads(http_get(MC_MANIFEST))["versions"]]


def mc_notes_text(v):
    """The whole patch note as plain text, so the Arabic summary can pick the real highlights."""
    text = v.get("text") or ""
    if v.get("path"):
        try:
            text += "\n" + plain(json.loads(http_get(MOJANG + "/v2/" + v["path"])).get("body") or "")
        except Exception:
            pass
    return text[:4000]


def mc_version_embed(v):
    """(embed, written) — written is False when Gemini couldn't write the Arabic summary."""
    vid = v["id"]
    how = "من اللانشر: Installations ← New ← اختار النسخة"
    if v["type"] == "release":
        kind, how = "✅ **تحديث رسمي نزل!**", "حدّث اللعبة من اللانشر وجرّب الإضافات الجديدة 🎮"
    elif re.search(r"rc", vid, re.I):
        kind = "🚀 **Release Candidate** — التحديث الرسمي صار قريب جداً"
    elif re.search(r"pre", vid, re.I):
        kind = "🧪 **Pre-Release** — آخر مرحلة قبل التحديث الرسمي"
    else:
        kind = "🧪 **Snapshot** — نسخة تجريبية فيها أشياء جديدة لسا ما نزلت رسمياً"
    lines = [kind]
    ai = arabic.rewrite("ملاحظات تحديث ماين كرافت", f"Minecraft {vid}", mc_notes_text(v), "Mojang")
    if ai:
        lines.append(ai["summary"])
        if ai["points"]:
            lines.append("✨ **أبرز الجديد:**\n" + bullets(ai["points"]))
    elif v.get("text"):
        text = v["text"].strip()
        lines.append("📝 " + (text if len(text) <= 320 else text[:320].rsplit(" ", 1)[0] + "…"))
    lines.append(how)
    e = {"title": f"⛏️ ماين كرافت {vid}",
         "url": "https://minecraft.wiki/?search=" + urllib.parse.quote("Java Edition " + vid),
         "description": "\n\n".join(lines)[:1500], "color": 0x5FAD41,
         "footer": {"text": "ماين كرافت Java — من Mojang"}, "timestamp": v.get("time")}
    if v.get("image"):
        e["image"] = {"url": v["image"]}
    return e, ai is not None


def run_mc_versions(st, webhook):
    vs = mc_versions()
    known = set(st.get("versions", []))
    if not known:
        post(webhook, MC_NAME, {"content": "✅ **تسريبات ماين كرافت اشتغلت!** كل نسخة تجريبية أو تحديث جديد من Mojang، "
                                           "وأهم أخبار ماين كرافت، رح تنزل هون أول بأول ⛏️\nهاي آخر نسخة نزلت:"})
        post(webhook, MC_NAME, {"embeds": [mc_version_embed(vs[0])[0]]})
        st["versions"] = [v["id"] for v in vs[:60] if v["id"]]
        print("   minecraft versions: first run, sample posted")
        return
    new = [v for v in vs[:25] if v["id"] and v["id"] not in known]
    retry, done, held = st.get("retry", {}), [], set()
    for v in reversed(new[:3]):
        embed, written = mc_version_embed(v)
        if hold(retry, v["id"], written):
            held.add(v["id"])
            continue
        post(webhook, MC_NAME, with_ping({"embeds": [embed]}, ROLE_MC))
        done.append(v["id"])
    st["retry"] = {k: n for k, n in retry.items() if k in held}
    st["versions"] = (list(known) + [v["id"] for v in new if v["id"] not in held])[-400:]
    print(f"   minecraft versions: {len(new)} new, posted {len(done)}" + (f", {len(held)} held" if held else ""))


def mc_official_embed(e):
    """(embed, written) for one of Mojang's own news cards."""
    text = (e.get("text") or "").strip()
    ai = arabic.rewrite("خبر رسمي من Mojang", e.get("title") or "",
                        (text + "\n" + plain(e.get("articleBody") or ""))[:4000], "Mojang")
    if ai:
        title = f"📢 {ai['title']}"
        lines = [ai["summary"]] + ([bullets(ai["points"])] if ai["points"] else [])
    else:
        title, lines = f"📢 {e.get('title')}", [text[:350]] if text else []
    lines.append(f"🟩 خبر رسمي من Mojang • {e.get('category')}")
    embed = {"title": title[:250], "url": e.get("readMoreLink"), "description": "\n\n".join(lines)[:1200],
             "color": 0x5FAD41, "footer": {"text": "أخبار ماين كرافت الرسمية"}}
    banner = (e.get("newsPageImage") or e.get("playPageImage") or {}).get("url")
    if banner:
        embed["image"] = {"url": MOJANG + banner}
    return embed, ai is not None


def run_mc_official(st, webhook):
    """Mojang's own news (the cards in the Minecraft launcher), each with its banner."""
    entries = [e for e in json.loads(http_get(MOJANG + "/v2/news.json"))["entries"]
               if "Education" not in (e.get("category") or "")]
    entries.sort(key=lambda e: e.get("date") or "", reverse=True)
    seen = set(st.get("seen", []))
    fresh = [e for e in entries if e.get("id") and e["id"] not in seen]
    first = not seen
    chosen = fresh[:1] if first else fresh[:3]           # first run: just the latest, as a sample
    retry, done, held = st.get("retry", {}), 0, set()
    for e in reversed(chosen):
        embed, written = mc_official_embed(e)
        if hold(retry, e["id"], written):
            held.add(e["id"])
            continue
        post(webhook, MC_NAME, with_ping({"embeds": [embed]}, None if first else ROLE_MC))
        done += 1
    st["retry"] = {k: n for k, n in retry.items() if k in held}
    st["seen"] = (list(seen) + [e["id"] for e in entries if e.get("id") and e["id"] not in held])[-500:]
    print(f"   minecraft official news: {len(fresh)} new, posted {done}" + (f", {len(held)} held" if held else ""))


def run_minecraft(st, webhook):
    st.pop("news", None)                       # the old English Google News headlines are replaced
    for label, fn, sub in (("versions", run_mc_versions, st),
                           ("official", run_mc_official, st.setdefault("official", {})),
                           ("arabic", lambda s, w: run_feeds(s, w, MC_NAME, MC_AR_KEEP, None, "أخبار ماين كرافت",
                                                             0x5FAD41, per_run=2, per_day=4, role=ROLE_MC),
                            st.setdefault("arabic", {}))):
        try:
            fn(sub, webhook)
        except Exception as e:
            print(f"   minecraft {label} failed: {e!r}")


# ---------------------------------------------------------------- GTA
GTA_NAME = "تسريبات أبود 🚗"
GTA_KEEP = re.compile(r"GTA\s*(6|VI|5|V\b|Online|اونلاين|أونلاين)|جي\s*تي\s*[اأإ]ي|روكستار|Rockstar", re.I)
GTA_NOISE = re.compile(r"غاز|عملة|gtaification|crypto|بطاقة|youtube|يوتيوب|تحميل|apk|شراء|بيتكوين|سهم|بورصة|مجانا|vietnam", re.I)


def run_gta(st, webhook):
    run_feeds(st, webhook, GTA_NAME, GTA_KEEP, GTA_NOISE, "أخبار وتسريبات GTA 5 و GTA 6", 0xF59E0B,
              intro="✅ **تسريبات GTA اشتغلت!** كل خبر أو تسريب جديد عن GTA 6 و GTA 5 من المواقع العربية رح ينزل هون "
                    "تلقائياً 🚗🔥", per_run=3, per_day=10, role=ROLE_GTA)


# ---------------------------------------------------------------- main
def selftest():
    """Print (in the Actions log, nothing is posted) a sample Arabic rewrite, to prove the Gemini key works."""
    print("selftest: GEMINI_API_KEY is", "set" if arabic.enabled() else "MISSING")
    try:
        items = parse_rss(http_get(AR_FEEDS[0][0]), AR_FEEDS[0][1])
        e, ok, _ = news_embed(next(x for x in items if GTA_KEEP.search(x["title"])), "selftest", 0xF59E0B)
        print("selftest GTA:", "rewritten" if ok else "ORIGINAL",
              json.dumps({"title": e["title"], "description": e["description"]}, ensure_ascii=False))
        m, ok = mc_version_embed(mc_versions()[0])
        print("selftest MC:", "rewritten" if ok else "ORIGINAL",
              json.dumps({"title": m["title"], "description": m["description"]}, ensure_ascii=False))
    except Exception as ex:
        print("selftest failed:", repr(ex))


def main():
    if os.environ.get("LEAKS_SELFTEST") == "1":
        selftest()
    state = load_state()
    jobs = [("fortnite", "DISCORD_WEBHOOK", run_fortnite),
            ("minecraft", "DISCORD_WEBHOOK_MINECRAFT", run_minecraft),
            ("gta", "DISCORD_WEBHOOK_GTA", run_gta)]
    for key, env, fn in jobs:
        hook = os.environ.get(env, "").strip()
        if not hook and not DRY_RUN:
            print(f"{key}: {env} not set yet — skipped")
            continue
        print(f"{key}:")
        try:
            fn(state.setdefault(key, {}), hook)
        except Exception as e:                  # one broken source must not stop the others
            print(f"   {key} failed: {e!r}")
    if not DRY_RUN:
        save_state(state)


if __name__ == "__main__":
    main()
