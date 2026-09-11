"""Rewrites a news item as short, clear Arabic with Gemini (free tier), so every post reads alike.

Without a GEMINI_API_KEY, or when every attempt fails, rewrite() returns None and the caller
decides: hold the item for the next run, or post the original text.
"""
import json
import os
import time
import urllib.error
import urllib.request

MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash"]   # in order of preference
URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"
PROMPT = """أنت محرر أخبار ألعاب لسيرفر ديسكورد عربي اسمه «تسريبات أبود».
أعد كتابة الخبر التالي بالعربية الفصحى البسيطة: عنوان قصير جذاب، وملخص من جملة أو جملتين، بأسلوب واضح وممتع بدون مبالغة.
قواعد مهمة:
- التزم بالمعلومات الموجودة في النص فقط، ولا تخترع أي تفاصيل أو أرقام أو تواريخ أو أسعار.
- اترك أسماء الألعاب والشركات والإصدارات بالإنجليزية كما هي (مثل GTA 6 و Rockstar و Minecraft 26.3).
- العنوان أقل من 70 حرفاً، والملخص أقل من 220 حرفاً.
- إذا ذكر النص مميزات أو تغييرات، اكتب أبرز 3 منها كنقاط قصيرة جداً، وإلا اترك النقاط فارغة.
- important = true فقط إذا كان الخبر رسمياً ومهماً فعلاً: إعلان، موعد إصدار، تريلر، تأجيل، أو تسريب كبير.
- emoji: إيموجي واحد يناسب الخبر.

نوع الخبر: {kind}
المصدر: {source}
العنوان الأصلي: {title}
النص:
{text}"""
SCHEMA = {"type": "OBJECT", "required": ["title", "summary", "important", "emoji"], "properties": {
    "title": {"type": "STRING"}, "summary": {"type": "STRING"},
    "points": {"type": "ARRAY", "items": {"type": "STRING"}},
    "important": {"type": "BOOLEAN"}, "emoji": {"type": "STRING"}}}
BUSY = {500, 502, 503, 504}                     # Google's free tier answers 503 when a model is overloaded


def enabled():
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def rewrite(kind, title, text, source=""):
    """{title, summary, points, important, emoji} in Arabic, or None."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key or not title:
        return None
    body = json.dumps({
        "contents": [{"parts": [{"text": PROMPT.format(kind=kind, source=source or "—", title=title,
                                                        text=(text or title)[:4000])}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json", "responseSchema": SCHEMA},
    }).encode("utf-8")
    last = "no answer"
    for model in MODELS:
        for attempt in range(2):                # a busy model often answers a few seconds later
            req = urllib.request.Request(URL.format(model), data=body,
                                         headers={"Content-Type": "application/json", "x-goog-api-key": key})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    out = json.loads(json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"])
            except urllib.error.HTTPError as e:
                try:
                    err = json.loads(e.read() or b"{}").get("error") or {}
                except ValueError:
                    err = {}
                msg = err.get("message") or ""
                last = f"{model}: HTTP {e.code} {err.get('status', '')} {msg[:160]}"   # Google's text never holds the key
                if e.code in BUSY and attempt == 0:
                    time.sleep(5)
                    continue                    # same model, once more
                if e.code in BUSY or e.code in (404, 429) or (e.code == 400 and "model" in msg.lower()
                                                              and "key" not in msg.lower()):
                    break                       # busy, out of quota, or not offered: next model
                print(f"   arabic rewrite failed: {last}")
                return None
            except Exception as e:
                last = f"{model}: {type(e).__name__}"
                break
            t, s = (out.get("title") or "").strip(), (out.get("summary") or "").strip()
            if not t or not s:
                last = f"{model}: empty answer"
                break
            return {"title": t[:120], "summary": s[:400], "important": bool(out.get("important")),
                    "emoji": (out.get("emoji") or "").strip()[:4],
                    "points": [p.strip()[:140] for p in (out.get("points") or []) if p and p.strip()][:3]}
    print(f"   arabic rewrite failed on every model (last: {last})")
    return None
