"""Rewrites a news item as short, clear Arabic with Gemini (free tier), so every post reads alike.

Without a GEMINI_API_KEY, or when the call fails, rewrite() returns None and the caller
keeps the original text — the bot never stops posting because of this step.
"""
import json
import os
import urllib.error
import urllib.request

MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-2.5-flash"]   # first one that answers
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
    for model in MODELS:
        req = urllib.request.Request(URL.format(model), data=body,
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"])
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):            # this model name isn't offered to the key: try the next
                continue
            print(f"   arabic rewrite failed: HTTP {e.code}")          # never print the key or the request
            return None
        except Exception as e:
            print(f"   arabic rewrite failed: {type(e).__name__}")
            return None
        t, s = (out.get("title") or "").strip(), (out.get("summary") or "").strip()
        if not t or not s:
            return None
        return {"title": t[:120], "summary": s[:400], "important": bool(out.get("important")),
                "emoji": (out.get("emoji") or "").strip()[:4],
                "points": [p.strip()[:140] for p in (out.get("points") or []) if p and p.strip()][:3]}
    print("   arabic rewrite failed: no model answered (check the key)")
    return None
