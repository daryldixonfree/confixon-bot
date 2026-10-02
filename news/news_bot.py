import hashlib
import json
import os
import random
import re
import xml.etree.ElementTree as ET

import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL = os.environ.get("NEWS_CHANNEL_NAME")
AI_API_KEY = os.environ.get("CODECRAFT_API_KEY")
AI_BASE_URL = "https://codecraftapi.com/v1"
AI_MODEL = os.environ.get("CODECRAFT_MODEL") or "deepseek-v4-flash-0731"

# فقط منابع غیرسیاسی: تکنولوژی، علم، فضا، اخبار عجیب
FEEDS = [
    "https://www.space.com/feeds/all",
    "https://www.sciencedaily.com/rss/all.xml",
    "https://techcrunch.com/feed/",
    "http://www.odditycentral.com/feed",
]

SEEN_FILE = "news/seen_news.json"
MAX_SEEN = 500
MAX_TRIES = 6  # حداکثر چندتا خبر رو به AI پیشنهاد بده تا یکی قبول بشه


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            return json.load(open(SEEN_FILE))
        except Exception:
            pass
    return []


def save_seen(seen):
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    json.dump(seen[-MAX_SEEN:], open(SEEN_FILE, "w"))


def h(text):
    return hashlib.md5(text.encode()).hexdigest()


def strip_html(text):
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def fetch_feed(url):
    """فید RSS رو دستی پارس می‌کنه (بدون کتابخونه‌ی اضافه)."""
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.content)
        items = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            desc = strip_html(item.findtext("description") or "")[:500]
            link = (item.findtext("link") or "").strip()
            if title and link:
                items.append({"title": title, "desc": desc, "link": link})
        return items
    except Exception:
        return []


def fetch_all():
    items = []
    for url in FEEDS:
        items += fetch_feed(url)
    return items


def ai_rewrite(item):
    """خبر رو به یه پست فارسی کوتاه تبدیل می‌کنه، یا None اگه سیاسی/حساس تشخیص بده."""
    prompt = (
        "این خبر رو به فارسی، در ۲ تا ۳ جمله‌ی جذاب و خودمونی برای یه کانال تلگرامی "
        "بازنویسی کن. یکی دو تا ایموجی مرتبط اضافه کن. فقط خود متن پست رو بنویس، "
        "بدون مقدمه یا توضیح اضافه. اگه خبر به سیاست، مذهب یا هر موضوع حساسی مرتبط "
        f"بود، فقط دقیقاً کلمه‌ی SKIP رو بنویس و چیز دیگه‌ای ننویس.\n\n"
        f"عنوان: {item['title']}\nخلاصه: {item['desc']}"
    )
    try:
        r = requests.post(
            f"{AI_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}"},
            json={"model": AI_MODEL, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        data = r.json()
        if "choices" not in data:
            print(f"[دیباگ] جواب غیرمنتظره از AI (کد {r.status_code}): {data}")
            return None
        text = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[دیباگ] خطا تو تماس با AI: {type(e).__name__}: {e}")
        return None
    if text.upper().startswith("SKIP"):
        print(f"[دیباگ] AI این خبر رو رد کرد: {item['title']}")
        return None
    return text


def send(text, link):
    msg = f"{text}\n\n🔗 {link}"
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHANNEL, "text": msg, "disable_web_page_preview": False},
        timeout=30,
    )
    if not r.ok:
        print("Telegram error:", r.text)
    return r.ok


def run_once():
    items = fetch_all()
    if not items:
        return "هیچ خبری از فیدها پیدا نشد"
    seen = load_seen()
    seen_set = set(seen)
    random.shuffle(items)

    tries = 0
    for item in items:
        key = h(item["link"])
        if key in seen_set:
            continue
        tries += 1
        if tries > MAX_TRIES:
            break
        text = ai_rewrite(item)
        if text is None:
            seen.append(key)  # سیاسی/حساس بود؛ دیگه پیشنهادش نده
            continue
        if send(text, item["link"]):
            seen.append(key)
            save_seen(seen)
            return "یک خبر ارسال شد"
        return "ارسال به تلگرام ناموفق بود"
    save_seen(seen)
    return "خبر تازه‌ی مناسبی پیدا نشد"


if __name__ == "__main__":
    print(run_once())
