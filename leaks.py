#!/usr/bin/env python3
"""Post newly datamined Fortnite cosmetics to a Discord channel, in Arabic.

Runs on a schedule (GitHub Actions). Source: fortnite-api.com /v2/cosmetics/new,
which lists the items added to the game files by the latest update.
Only items that were not posted before are sent; state lives in state.json.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

API = "https://fortnite-api.com/v2/cosmetics/new?language=ar"
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
DRY_RUN = os.environ.get("DRY_RUN") == "1"

BOT_NAME = "تسريبات أبود 🔥"
AVATAR = ("https://yt3.googleusercontent.com/64sSctZiJSIBBsfI_R_tWo2tV3bYF2LP0xr5Mc6SPFurxKFMqe1m02dQ2z7MgvhIxX8pybPs"
          "=s256-c-k-c0x00ffffff-no-rj")
MAX_ITEMS = 30          # per update; the rest is summarised in one line
FIRST_RUN_SAMPLE = 6    # first run only shows a sample, so old leaks don't flood the channel
UA = {"User-Agent": "abod-leaks-bot/1.0"}

# rarity value -> (sort weight, embed colour)
RARITY = {
    "mythic": (9, 0xF4C430), "legendary": (8, 0xF39C12), "icon": (8, 0x2EC4DC),
    "marvel": (8, 0xC0392B), "dc": (8, 0x3A6BC7), "starwars": (8, 0x3B3B3B),
    "gaminglegends": (8, 0x6C3FC7), "shadow": (7, 0x505050), "slurp": (7, 0x13C8C8),
    "frozen": (7, 0x9FD8F0), "lava": (7, 0xD1491A), "dark": (7, 0xB22CB2),
    "epic": (6, 0x9B59B6), "rare": (4, 0x3498DB), "uncommon": (2, 0x2ECC71), "common": (1, 0x95A5A6),
}
TYPE_WEIGHT = {"outfit": 5, "backpack": 3, "pickaxe": 3, "glider": 3, "emote": 3, "wrap": 2}


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def post(payload):
    payload = {"username": BOT_NAME, "avatar_url": AVATAR, "allowed_mentions": {"parse": []}, **payload}
    if DRY_RUN:
        print("DRY:", json.dumps(payload, ensure_ascii=False)[:300])
        return
    url = WEBHOOK + ("&" if "?" in WEBHOOK else "?") + "wait=true"
    data = json.dumps(payload).encode("utf-8")
    for _ in range(6):
        req = urllib.request.Request(url, data=data, headers={**UA, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
            time.sleep(1.2)
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                body = e.read() or b"{}"
                time.sleep(float(json.loads(body).get("retry_after", 2)) + 0.5)
                continue
            raise
    raise RuntimeError("Discord kept rate-limiting the webhook")


def weight(item):
    rarity = ((item.get("rarity") or {}).get("value") or "").lower()
    kind = ((item.get("type") or {}).get("value") or "").lower()
    return RARITY.get(rarity, (0, 0))[0] * 10 + TYPE_WEIGHT.get(kind, 1)


def embed(item):
    rarity = item.get("rarity") or {}
    series = item.get("series") or {}
    kind = (item.get("type") or {}).get("displayValue") or ""
    tag = series.get("value") or rarity.get("displayValue") or ""
    desc = " • ".join(x for x in (kind, tag) if x)
    set_text = (item.get("set") or {}).get("text")
    if set_text:
        desc += "\n" + set_text
    images = item.get("images") or {}
    icon = images.get("icon") or images.get("smallIcon")
    e = {
        "title": (item.get("name") or item.get("id") or "؟")[:250],
        "description": desc[:400],
        "color": RARITY.get((rarity.get("value") or "").lower(), (0, 0x8B5CF6))[1],
    }
    if icon:
        e["thumbnail"] = {"url": icon}
    return e


def short_build(build):
    m = re.search(r"Release-(\d+\.\d+)", build or "")
    return m.group(1) if m else (build or "؟")


def main():
    data = get_json(API)["data"]
    items = (data.get("items") or {}).get("br") or []
    build = short_build(data.get("build"))
    state = load_state()

    if not WEBHOOK and not DRY_RUN:
        print("DISCORD_WEBHOOK secret is not set yet — nothing posted.")
        return

    posted = set(state.get("posted", []))
    first_run = not state
    fresh = [x for x in items if x.get("id") not in posted]
    if not fresh:
        print(f"no new leaks (build {build}, {len(items)} items already posted)")
        return

    fresh.sort(key=weight, reverse=True)
    if first_run:
        header = ("✅ **بوت التسريبات اشتغل!** من هلأ، كل ما ينزل تحديث لفورتنايت، رح توصلكم هون السكنات والرقصات "
                  f"والأدوات الجديدة اللي انضافت لملفات اللعبة 👀\nهاي عيّنة من آخر تحديث ({build}):")
        chosen = fresh[:FIRST_RUN_SAMPLE]
    else:
        header = (f"🔥 **تسريبات جديدة بفورتنايت!** — التحديث {build}\n"
                  f"انضاف {len(fresh)} عنصر جديد لملفات اللعبة (سكنات، رقصات، أدوات…) 👇")
        chosen = fresh[:MAX_ITEMS]

    post({"content": header})
    for i in range(0, len(chosen), 10):
        post({"embeds": [embed(x) for x in chosen[i:i + 10]]})
    extra = len(fresh) - len(chosen)
    if extra > 0 and not first_run:
        post({"content": f"➕ وكمان {extra} عنصر ثاني… رح يبينوا بالمتجر مع الوقت 👀"})

    if DRY_RUN:
        print(f"DRY RUN: would mark {len(items)} items as posted (build {build})")
        return
    state = {"build": data.get("build"), "date": data.get("date"),
             "posted": sorted(posted | {x.get("id") for x in items if x.get("id")})[-3000:]}
    save_state(state)
    print(f"posted {len(chosen)} items from build {build}")


if __name__ == "__main__":
    main()
