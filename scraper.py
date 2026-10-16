import base64
import html
import json
import os
import re
from urllib.parse import quote, unquote

import requests

# ================== تنظیمات جمع‌آوری (گیت‌هاب) ==================
CHANNEL = os.environ.get("CHANNEL_NAME", "@confixon")
ENV_CHANNELS = os.environ.get("TELEGRAM_CHANNELS", "")
if ENV_CHANNELS:
    SOURCE_CHANNELS = [c.strip().lstrip("@") for c in ENV_CHANNELS.split(",") if c.strip()]
else:
    SOURCE_CHANNELS = [
        "simchin_ir",
        "filembad",
        "capoit",
        "etehadvpn",
        "nufilter",
        "hddify",
        "confihub",
    ]

EXCLUDE = {"🇮🇷"}
REBRAND = True
OUTPUT_FILE = "configs.txt"
# ===============================================================

CFG_RE = re.compile(r"\b(?:vmess|vless|trojan|ss|hysteria2|hy2|tuic)://[^\r\n<>\"'\s]+")
FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
TAG_RE = re.compile(r"@[A-Za-z0-9_]{4,}")
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001F1E5\U0001F200-\U0001FAFF\u2190-\u21FF\u2300-\u23FF"
    "\u2600-\u27BF\u2B00-\u2BFF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3]"
)


def b64d(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def get_flag(cfg):
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
    if REBRAND:
        if TAG_RE.search(label):
            label = TAG_RE.sub(lambda m: CHANNEL, label)
        else:
            label = f"{CHANNEL} {label}"
    label = EMOJI_RE.sub(" ", label)
    return re.sub(r"\s+", " ", label).strip()


def clean_config(cfg):
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
        return body + ("#" + quote(label, safe="@:._-") if label else "")
    return f"{cfg}#{quote(CHANNEL, safe='@:._-')}"


def fetch_from_channel(ch):
    url = f"https://t.me/s/{ch}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(url, headers=headers, timeout=20)
    if not r.ok:
        return []
    text = html.unescape(r.text)
    found = [c.strip() for c in CFG_RE.findall(text) if c.strip()]
    # پیام‌های جدیدتر در پایین صفحه وب تلگرام هستند؛ برعکس می‌کنیم تا جدیدترین‌ها اول باشند
    found.reverse()
    return found


def main():
    all_configs = []
    for ch in SOURCE_CHANNELS:
        try:
            ch_configs = fetch_from_channel(ch)
            print(f"[+] کانال @{ch}: تعداد {len(ch_configs)} کانفیگ استخراج شد.")
            all_configs.extend(ch_configs)
        except Exception as e:
            print(f"[-] خطا در خواندن کانال @{ch}: {e}")

    cleaned = []
    seen_raw = set()
    for cfg in all_configs:
        if get_flag(cfg) in EXCLUDE:
            continue
        c_clean = clean_config(cfg)
        if c_clean not in seen_raw:
            seen_raw.add(c_clean)
            cleaned.append(c_clean)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(cleaned))

    print(f"[✓] مجموعاً {len(cleaned)} کانفیگ یکتا و تمیز در {OUTPUT_FILE} ذخیره شد.")


if __name__ == "__main__":
    main()
