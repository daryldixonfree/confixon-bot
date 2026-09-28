import base64
import hashlib
import html
import json
import os
import random
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote

import requests

# ================== تنظیمات ==================
# تو گیت‌هاب این‌ها از Secrets خونده می‌شن؛ برای تست روی سیستم خودت می‌تونی
# مقدار پیش‌فرض (بعد از or) رو موقتاً پر کنی.
TOKEN = os.environ.get("BOT_TOKEN") or 
CHANNEL = os.environ.get("CHANNEL_NAME") or 
SOURCE_URL = os.environ.get("BOT_SOURCE_URL_P") or 

TOTAL = 5                # تعداد کانفیگ در هر پیام
EXCLUDE = {"🇮🇷"}         # کانفیگ‌های این پرچم‌ها هیچ‌وقت فرستاده نمی‌شن
REBRAND = True            # آیدی کانال منبع تو اسم کانفیگ با CHANNEL عوض بشه
PING_TEST = True          # قبل از ارسال چک بشه سرور واقعاً جواب می‌ده یا نه
PING_TIMEOUT = 5          # ثانیه، برای هر تلاش اتصال مستقیم
USE_QUOTE = False         # True = نقل‌قول (ولی با یه تپ کپی نمی‌شه)
SEEN_FILE = "seen.json"
MAX_SEEN = 500
# ============================================

CFG_RE = re.compile(r"\b(?:vmess|vless|trojan|ss|hysteria2|hy2|tuic)://[^\r\n<>\"']+")
FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
TAG_RE = re.compile(r"@[A-Za-z0-9_]{4,}")
# همه‌ی ایموجی‌ها و نمادها به‌جز پرچم کشورها (U+1F1E6 تا U+1F1FF)
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001F1E5\U0001F200-\U0001FAFF\u2190-\u21FF\u2300-\u23FF"
    "\u2600-\u27BF\u2B00-\u2BFF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3]"
)
HP_RE = re.compile(r"@(\[[^\]]+\]|[^@/?#:\s]+):(\d+)")
UDP_RE = re.compile(r"^(?:hysteria2|hy2|tuic)://|[?&]type=(?:kcp|quic)\b")


def b64d(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            return json.load(open(SEEN_FILE))
        except Exception:
            pass
    return []


def save_seen(seen):
    json.dump(seen[-MAX_SEEN:], open(SEEN_FILE, "w"))


def h(cfg):
    return hashlib.md5(cfg.encode()).hexdigest()


def fetch_configs():
    r = requests.get(SOURCE_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    text = html.unescape(r.text)
    found = CFG_RE.findall(text)
    if not found:  # شاید ساب base64 باشه
        try:
            raw = re.sub(r"\s+", "", text)
            found = CFG_RE.findall(b64d(raw).decode("utf-8", "ignore"))
        except Exception:
            pass
    found = [c.strip() for c in found]
    return list(dict.fromkeys(found))  # حذف تکراری با حفظ ترتیب


def get_flag(cfg):
    """پرچم کشور رو از اسم کانفیگ درمیاره."""
    label = ""
    if cfg.startswith("vmess://"):
        try:
            label = json.loads(b64d(cfg[8:])).get("ps", "")
        except Exception:
            pass
    elif "#" in cfg:
        label = unquote(cfg.split("#", 1)[1])
    m = FLAG_RE.search(label) or FLAG_RE.search(unquote(cfg))
    return m.group(0) if m else None


def clean_label(label):
    """ایموجی‌ها رو (به‌جز پرچم) برمی‌داره و آیدی کانال خودت رو تو اسم می‌ذاره."""
    if REBRAND:
        if TAG_RE.search(label):
            label = TAG_RE.sub(lambda m: CHANNEL, label)
        else:
            label = f"{CHANNEL} {label}"
    label = EMOJI_RE.sub(" ", label)
    return re.sub(r"\s+", " ", label).strip()


def clean_config(cfg):
    """اسم کانفیگ رو تمیز می‌کنه؛ خروجی بدون فاصله و بدون خط خالی."""
    if cfg.startswith("vmess://"):
        try:
            d = json.loads(b64d(cfg[8:]))
            d["ps"] = clean_label(d.get("ps", ""))
            raw = json.dumps(d, ensure_ascii=False).encode("utf-8")
            return "vmess://" + base64.b64encode(raw).decode()
        except Exception:
            return cfg
    if "#" in cfg:
        body, label = cfg.split("#", 1)
        label = clean_label(unquote(label))
        # فاصله‌ها به %20 تبدیل می‌شن تا کانفیگ یه تکه بمونه
        return body + ("#" + quote(label, safe="@:._-") if label else "")
    return cfg


def get_host_port(cfg):
    scheme, rest = cfg.split("://", 1)
    body = rest.split("#", 1)[0]
    if scheme == "vmess":
        try:
            d = json.loads(b64d(body))
            return d["add"], int(d["port"])
        except Exception:
            return None
    main = body.split("?", 1)[0]
    found = HP_RE.findall(main)
    if not found and scheme == "ss":  # ss قدیمی: همه‌چیز داخل base64 هست
        try:
            found = HP_RE.findall(b64d(main).decode("utf-8", "ignore"))
        except Exception:
            pass
    if not found:
        return None
    host, port = found[-1]
    return host.strip("[]"), int(port)


def tcp_ping(cfg):
    """میلی‌ثانیه اگه سرور جواب بده، وگرنه None. اتصال مستقیمه چون
    رانر گیت‌هاب محدودیت شبکه‌ی PythonAnywhere رو نداره. پروتکل‌های
    UDP قابل تست نیستن و بدون تست قبول می‌شن."""
    if UDP_RE.search(cfg):
        return 0.0
    hp = get_host_port(cfg)
    if not hp:
        return None
    start = time.time()
    try:
        with socket.create_connection(hp, timeout=PING_TIMEOUT):
            pass
    except OSError:
        return None
    return (time.time() - start) * 1000


def pick(configs, seen):
    seen_set = set(seen)
    configs = [c for c in configs if get_flag(c) not in EXCLUDE]
    # اول کانفیگ‌های نفرستاده‌شده، بعد بقیه؛ داخل هر دسته تصادفی
    ordered = sorted(configs, key=lambda c: (h(c) in seen_set, random.random()))

    def alive(cands):
        """فقط کانفیگ‌های زنده (به همون ترتیب). پینگ‌ها هم‌زمان گرفته می‌شن."""
        if not PING_TEST:
            return list(cands)

        def safe_ping(c):
            try:
                return tcp_ping(c)
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=32) as ex:
            results = list(ex.map(safe_ping, cands))
        return [c for c, p in zip(cands, results) if p is not None]

    chosen = []
    rest = ordered
    while len(chosen) < TOTAL and rest:
        chunk, rest = rest[:20], rest[20:]
        chosen += alive(chunk)
    return chosen[:TOTAL]


def build_message(chosen):
    while True:
        lines = "\n".join(html.escape(clean_config(c), quote=False) for c in chosen)
        if USE_QUOTE:
            box = f"<blockquote expandable>{lines}</blockquote>"
        else:
            box = f"<pre>{lines}</pre>"
        msg = f"🔐 کانفیگ‌های جدید\n\n{box}\n\n{CHANNEL}"
        if len(msg) <= 4000 or len(chosen) <= 1:
            return msg
        chosen = chosen[:-1]  # اگه از حد تلگرام بیشتر شد، آخری رو بردار


def send(msg):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHANNEL, "text": msg, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )
    if not r.ok:
        print("Telegram error:", r.text)
    return r.ok


def run_once():
    configs = fetch_configs()
    if not configs:
        return "هیچ کانفیگی پیدا نشد"
    seen = load_seen()
    chosen = pick(configs, seen)
    if not chosen:
        return "هیچ کانفیگ زنده‌ای پیدا نشد"
    if send(build_message(chosen)):
        seen += [h(c) for c in chosen]
        save_seen(seen)
        return f"{len(chosen)} کانفیگ ارسال شد"
    return "ارسال به تلگرام ناموفق بود"


if __name__ == "__main__":
    print(run_once())
