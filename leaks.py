#!/usr/bin/env python3
"""Leaks & news poster for the أبود قنّاص Discord — Fortnite, Minecraft and GTA.

Runs on GitHub Actions about every 10 minutes. Each game posts to its own channel through
its own webhook secret; a game whose secret is missing is simply skipped.

  fortnite   DISCORD_WEBHOOK            new game versions, items datamined into the game files and
                                        what is new in the item shop (fortnite-api.com), drawn as one
                                        zoomable picture (cards.py) plus Epic's clips of the emotes
  minecraft  DISCORD_WEBHOOK_MINECRAFT  new Java snapshots/pre-releases/releases (Mojang) + news
  gta        DISCORD_WEBHOOK_GTA        Arabic GTA 5 / GTA 6 news headlines (Google News)

Everything already posted is remembered in state.json, so nothing is sent twice.
"""
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

import cards

STATE = os.environ.get("LEAKS_STATE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
PREVIEW = os.environ.get("PREVIEW_DIR")          # with DRY_RUN, pictures are written here instead of sent
AVATAR = ("https://yt3.googleusercontent.com/64sSctZiJSIBBsfI_R_tWo2tV3bYF2LP0xr5Mc6SPFurxKFMqe1m02dQ2z7MgvhIxX8pybPs"
          "=s256-c-k-c0x00ffffff-no-rj")
UA = {"User-Agent": "Mozilla/5.0 (compatible; abod-leaks-bot/1.3)"}


# ---------------------------------------------------------------- helpers
def http_get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


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


def post(webhook, name, payload):
    payload = {"username": name, "avatar_url": AVATAR, "allowed_mentions": {"parse": []}, **payload}
    if DRY_RUN:
        print("   DRY:", json.dumps(payload, ensure_ascii=False)[:260])
        return
    send(webhook, json.dumps(payload).encode("utf-8"), "application/json")


def post_file(webhook, name, payload, filename, data):
    """A message with one attached picture, which an embed can show as attachment://<filename>."""
    payload = {"username": name, "avatar_url": AVATAR, "allowed_mentions": {"parse": []}, **payload}
    if DRY_RUN:
        if PREVIEW:
            with open(os.path.join(PREVIEW, filename), "wb") as f:
                f.write(data)
        print(f"   DRY: {filename} ({len(data) // 1024} KB) +", json.dumps(payload, ensure_ascii=False)[:200])
        return
    boundary = "abodleaks" + os.urandom(8).hex()
    body = b"".join([
        (f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
         "Content-Type: application/json\r\n\r\n").encode(), json.dumps(payload).encode("utf-8"),
        (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{filename}"\r\n'
         "Content-Type: image/jpeg\r\n\r\n").encode(), data, f"\r\n--{boundary}--\r\n".encode()])
    send(webhook, body, f"multipart/form-data; boundary={boundary}")


def parse_rss(raw):
    items = []
    for it in ET.fromstring(raw).iter("item"):
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            pub = datetime.now(timezone.utc)
        items.append({"title": title, "link": (it.findtext("link") or "").strip(), "source": source, "pub": pub})
    return items


def norm(title):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", title.lower())).strip()[:60]


def run_news(st, webhook, name, feed, keep, noise, label, color, intro, per_run=3, per_day=10, max_age_h=24):
    """Post fresh, relevant headlines from an RSS feed; remember links and titles."""
    items = parse_rss(http_get(feed))
    seen = set(st.get("seen", []))
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"], st["count"] = today, 0

    relevant = [it for it in items
                if it["link"] not in seen and norm(it["title"]) not in seen
                and (not keep or keep.search(it["title"]))
                and not (noise and noise.search(it["title"] + " " + it["source"]))
                and now - it["pub"] <= timedelta(hours=max_age_h)]
    relevant.sort(key=lambda x: x["pub"], reverse=True)
    batch, keys = [], set()                   # the same story from several sites → keep one
    for it in relevant:
        key = norm(it["title"])[:35]
        if key in keys:
            continue
        keys.add(key)
        batch.append(it)

    first = not st.get("seen")
    room = max(0, per_day - st.get("count", 0))
    chosen = batch[: (2 if first else min(per_run, room))]
    if first:
        post(webhook, name, {"content": intro})
    for it in reversed(chosen):
        post(webhook, name, {"embeds": [{
            "title": it["title"][:250], "url": it["link"], "description": ("📰 " + it["source"])[:300],
            "color": color, "footer": {"text": label}, "timestamp": it["pub"].isoformat()}]})
    st["count"] = st.get("count", 0) + (0 if first else len(chosen))
    st["seen"] = (list(seen) + [x for it in items for x in (it["link"], norm(it["title"]))])[-1500:]
    print(f"   {label}: {len(items)} in feed, {len(batch)} relevant, posted {len(chosen)}")


# ---------------------------------------------------------------- Fortnite
FN_API = "https://fortnite-api.com/v2/cosmetics/new?language=ar"
FN_AES = "https://fortnite-api.com/v2/aes"                       # its build changes the moment a version ships
FN_SHOP = "https://fortnite-api.com/v2/shop?language=ar"
FN_ITEM = "https://fortnite-api.com/v2/cosmetics/br/"
FN_NAME = "تسريبات أبود 🔥"
FOOTER = "تسريبات أبود  •  كل جديد فورتنايت أول بأول  •  youtube.com/@Ab_sn6"
SHOP_DESIGN = 2                                # bump to re-post today's shop once in a new look
RARITY = {
    "mythic": (9, 0xF4C430), "legendary": (8, 0xF39C12), "icon": (8, 0x2EC4DC),
    "marvel": (8, 0xC0392B), "dc": (8, 0x3A6BC7), "starwars": (8, 0x3B3B3B),
    "gaminglegends": (8, 0x6C3FC7), "shadow": (7, 0x505050), "slurp": (7, 0x13C8C8),
    "frozen": (7, 0x9FD8F0), "lava": (7, 0xD1491A), "dark": (7, 0xB22CB2),
    "epic": (6, 0x9B59B6), "rare": (4, 0x3498DB), "uncommon": (2, 0x2ECC71), "common": (1, 0x95A5A6),
}
TYPE_WEIGHT = {"outfit": 5, "backpack": 3, "pickaxe": 3, "glider": 3, "emote": 3, "wrap": 2}
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


def fn_weight(item):
    rarity = ((item.get("rarity") or {}).get("value") or "").lower()
    return RARITY.get(rarity, (0, 0))[0] * 10 + TYPE_WEIGHT.get(item_kind(item), 1)


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
        line = " • ".join(x for x in ((it.get("type") or {}).get("displayValue"),
                                      (it.get("series") or {}).get("value")
                                      or (it.get("rarity") or {}).get("displayValue")) if x)
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
        post(webhook, FN_NAME, {"embeds": [{
            "title": f"🆕 تحديث فورتنايت {ver} نزل!",
            "description": "حدّثوا اللعبة 🎮\nالسكنات والرقصات الجديدة اللي انضافت بالتحديث رح تنزل هون أول ما تبين 👀",
            "color": 0x8B5CF6, "footer": {"text": f"Fortnite v{ver}"},
            "timestamp": datetime.now(timezone.utc).isoformat()}]})
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
    top = fresh[:(6 if first else 30)]
    if first:
        title = "✅ بوت التسريبات اشتغل!"
        lines = ["من هلأ، كل ما ينزل تحديث لفورتنايت، رح توصلكم هون السكنات والرقصات والأدوات الجديدة "
                 f"اللي انضافت لملفات اللعبة 👀", f"هاي عيّنة من آخر تحديث ({build})"]
    else:
        title = "🔥 تسريبات جديدة بفورتنايت!"
        lines = [f"🧩 التحديث {build}", f"🆕 انضاف {len(fresh)} عنصر جديد لملفات اللعبة (سكنات، رقصات، أدوات…)"]
        if len(fresh) > len(top):
            lines.append(f"➕ وكمان {len(fresh) - len(top)} عنصر ثاني… رح يبينوا بالمتجر مع الوقت 👀")
    try:
        jpg = cards.draw_card("تسريبات فورتنايت", f"التحديث {build} • {len(fresh)} عنصر جديد بملفات اللعبة",
                              leak_tiles(top), FOOTER, AVATAR)
        post_file(webhook, FN_NAME, {"embeds": [card_embed(title, lines, "leaks.jpg")]}, "leaks.jpg", jpg)
    except Exception as e:                      # no Chrome / a broken image: fall back to plain embeds
        print(f"   leaks card failed ({e!r}) — sending embeds instead")
        post(webhook, FN_NAME, {"content": f"**{title}**\n" + "\n".join(lines)})
        for i in range(0, len(top), 10):
            post(webhook, FN_NAME, {"embeds": [fn_embed(x) for x in top[i:i + 10]]})
    videos = post_videos(webhook, [(x, None) for x in top], lookup=False)
    st.update({"build": data.get("build"), "date": data.get("date"),
               "posted": sorted(posted | {x.get("id") for x in items if x.get("id")})[-3000:]})
    print(f"   fortnite leaks: {len(fresh)} new from build {build}, card with {len(top)}, {videos} videos")


def shop_weight(entry):
    kinds = [item_kind(it) for it in entry["brItems"]]
    return (1 if entry.get("bundle") else 0, max(TYPE_WEIGHT.get(k, 1) for k in kinds), entry.get("finalPrice") or 0)


def shop_main(entry):
    return max(entry["brItems"], key=lambda it: TYPE_WEIGHT.get(item_kind(it), 1))


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
    e = {"title": (bundle.get("name") or main.get("name") or "؟")[:250], "description": "\n".join(lines)[:400],
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
        tiles.append({"name": bundle.get("name") or main.get("name") or "؟",
                      "price": e.get("finalPrice"), "regular": e.get("regularPrice"),
                      "badge": "باقة" if bundle else (main.get("type") or {}).get("displayValue"),
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
        name = (e.get("bundle") or {}).get("name") or e["brItems"][0].get("name")
        if name not in names:
            names.add(name)
            unique.append(e)
    if not unique:
        print("   fortnite shop: changed, nothing new")
        return
    top = unique[:30]
    if not known:
        title = "🛒 متجر فورتنايت اليوم"
        lines = [f"📅 {ar_date(date)} • 🆕 {len(unique)} عنصر جديد",
                 "من هلأ، كل ما يتجدد المتجر (الساعة 3 الفجر) رح ينزل هون الجديد فيه أول بأول"]
    elif new_day:
        title, lines = "🛒 متجر فورتنايت الجديد نزل!", [f"📅 {ar_date(date)} • 🆕 {len(unique)} عنصر جديد اليوم"]
    else:
        title, lines = "🛒 انضافت أشياء جديدة للمتجر!", [f"📅 {ar_date(date)} • 🆕 {len(unique)} عنصر جديد"]
    if len(unique) > len(top):
        lines.append(f"➕ وكمان {len(unique) - len(top)} عنصر — شوفوهم باللعبة 🎮")
    try:
        jpg = cards.draw_card("متجر فورتنايت", f"{ar_date(date)} • {len(unique)} عنصر جديد", shop_tiles(top),
                              FOOTER, AVATAR)
        post_file(webhook, FN_NAME, {"embeds": [card_embed(title, lines, "shop.jpg")]}, "shop.jpg", jpg)
    except Exception as e:                      # no Chrome / a broken image: fall back to plain embeds
        print(f"   shop card failed ({e!r}) — sending embeds instead")
        post(webhook, FN_NAME, {"content": f"**{title}**\n" + "\n".join(lines)})
        for i in range(0, min(20, len(top)), 10):
            post(webhook, FN_NAME, {"embeds": [shop_embed(e) for e in top[i:i + 10]]})
    pairs = [(it, e.get("finalPrice") if len(e["brItems"]) == 1 else None) for e in top for it in e["brItems"]]
    videos = post_videos(webhook, pairs, lookup=True)
    print(f"   fortnite shop: {len(unique)} new offers, card with {len(top)}, {videos} videos")


def run_fortnite(st, webhook):
    for label, fn, sub in (("version", run_fn_version, st), ("leaks", run_fn_leaks, st),
                           ("shop", run_fn_shop, st.setdefault("shop", {}))):
        try:
            fn(sub, webhook)
        except Exception as e:                  # one broken part must not stop the others
            print(f"   fortnite {label} failed: {e!r}")


# ---------------------------------------------------------------- Minecraft
MC_NAME = "تسريبات أبود ⛏️"
MC_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MC_NEWS = ("https://news.google.com/rss/search?q=intitle:Minecraft+(snapshot+OR+update+OR+drop+OR+leak+OR+"
           "%22Minecraft+Live%22+OR+beta)+when:2d&hl=en-US&gl=US&ceid=US:en")
MC_NOISE = re.compile(
    r"horion|client|hack|cheat|apk|mod menu|download|crack|youtube|seed|coupon|deal|price"
    r"|^Minecraft\s+[\d.]+.*(snapshot|pre-release|release candidate)",   # changelogs are covered by the version posts
    re.I)


def mc_version_embed(v):
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
    return {"title": f"⛏️ ماين كرافت {vid}",
            "url": "https://minecraft.wiki/?search=" + urllib.parse.quote("Java Edition " + vid),
            "description": f"{kind}\n{how}", "color": 0x5FAD41, "footer": {"text": "ماين كرافت Java — Mojang"},
            "timestamp": v.get("releaseTime")}


def run_minecraft(st, webhook):
    vs = json.loads(http_get(MC_MANIFEST))["versions"]
    known = set(st.get("versions", []))
    if not known:
        post(webhook, MC_NAME, {"content": "✅ **تسريبات ماين كرافت اشتغلت!** كل نسخة تجريبية أو تحديث جديد من Mojang، "
                                           "وأهم أخبار ماين كرافت، رح تنزل هون أول بأول ⛏️\nهاي آخر نسخة نزلت:"})
        post(webhook, MC_NAME, {"embeds": [mc_version_embed(vs[0])]})
        st["versions"] = [v["id"] for v in vs[:60]]
        print("   minecraft versions: first run, sample posted")
    else:
        new = [v for v in vs[:25] if v["id"] not in known]
        for v in reversed(new[:3]):
            post(webhook, MC_NAME, {"embeds": [mc_version_embed(v)]})
        st["versions"] = (list(known) + [v["id"] for v in new])[-400:]
        print(f"   minecraft versions: {len(new)} new")
    run_news(st.setdefault("news", {}), webhook, MC_NAME, MC_NEWS, re.compile(r"minecraft", re.I), MC_NOISE,
             "أخبار ماين كرافت", 0x5FAD41, "📰 ومن هلأ كمان أهم أخبار ماين كرافت رح تنزل هون.", per_run=2, per_day=5)


# ---------------------------------------------------------------- GTA
GTA_NAME = "تسريبات أبود 🚗"
GTA_NEWS = ("https://news.google.com/rss/search?q=intitle:GTA+OR+intitle:%22%D8%AC%D9%8A+%D8%AA%D9%8A+%D8%A7%D9%8A%22+OR+"
            "intitle:%D8%B1%D9%88%D9%83%D8%B3%D8%AA%D8%A7%D8%B1+when:2d&hl=ar&gl=JO&ceid=JO:ar")
GTA_KEEP = re.compile(r"GTA\s*(6|VI|5|V\b|Online|اونلاين|أونلاين)|جي\s*تي\s*[اأإ]ي|روكستار|Rockstar", re.I)
GTA_NOISE = re.compile(r"غاز|عملة|gtaification|crypto|بطاقة|youtube|يوتيوب|تحميل|apk|شراء|بيتكوين|سهم|بورصة|مجانا|vietnam", re.I)


def run_gta(st, webhook):
    run_news(st, webhook, GTA_NAME, GTA_NEWS, GTA_KEEP, GTA_NOISE, "أخبار وتسريبات GTA 5 و GTA 6", 0xF59E0B,
             "✅ **تسريبات GTA اشتغلت!** كل خبر أو تسريب جديد عن GTA 6 و GTA 5 من المواقع العربية رح ينزل هون "
             "تلقائياً 🚗🔥\nهاي آخر الأخبار:", per_run=3, per_day=10)


# ---------------------------------------------------------------- main
def main():
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
