import base64
import hashlib
import html
import json
import os
import random
import re
import socket
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, quote, unquote

import requests

# ================== تنظیمات ==================
TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL = os.environ.get("CHANNEL_NAME")
SOURCE_URLS = [u for u in (os.environ.get("BOT_SOURCE_URL_Y"), os.environ.get("BOT_SOURCE_URL_P")) if u]

TOTAL = 5
EXCLUDE = {"🇮🇷"}         # کانفیگ‌های این پرچم‌ها هیچ‌وقت فرستاده نمی‌شن
REBRAND = True            # آیدی کانال منبع تو اسم کانفیگ با CHANNEL عوض بشه
PING_TEST = True          # قبل از تست واقعی، اول فقط پورت رو چک کن (فیلتر اولیه‌ی سریع)
PING_TIMEOUT = 5          # ثانیه، برای هر تلاش اتصال مستقیم
XRAY_TEST = True          # تست واقعی با خود xray (اتصال واقعی، نه فقط باز بودن پورت)
XRAY_BIN = "./xray"       # مسیر فایل اجرایی xray (تو ورک‌فلو دانلود می‌شه)
XRAY_TIMEOUT = 8          # ثانیه، حداکثر انتظار برای جواب گرفتن از پشت کانفیگ
XRAY_TEST_URL = "https://web.telegram.org/"
XRAY_MAX_TEST = 40        # حداکثر چند کانفیگ رو با xray تست کنه (برای محدود کردن زمان اجرا)
USE_QUOTE = False
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
    for url in SOURCE_URLS:
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            text = html.unescape(r.text)
            found = CFG_RE.findall(text)
            if not found:
                try:
                    raw = re.sub(r"\s+", "", text)
                    found = CFG_RE.findall(b64d(raw).decode("utf-8", "ignore"))
                except Exception:
                    found = []
            found = [c.strip() for c in found]
            if found:
                return list(dict.fromkeys(found))
        except Exception:
            continue
    return []


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
    if not found and scheme == "ss":
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


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def cfg_to_xray_outbound(cfg):
    """کانفیگ رو به فرمت outbound خود xray تبدیل می‌کنه. اگه پروتکل یا نوع
    شبکه‌ش پشتیبانی نشه (مثل ss یا xhttp/kcp)، None برمی‌گردونه، و اون‌وقت
    فقط به همون تست پورت (tcp_ping) بسنده می‌کنیم."""
    scheme, rest = cfg.split("://", 1)
    body = rest.split("#", 1)[0]

    if scheme == "vmess":
        try:
            d = json.loads(b64d(body))
        except Exception:
            return None
        net = d.get("net", "tcp")
        stream = {"network": net}
        if net == "ws":
            stream["wsSettings"] = {"path": d.get("path") or "/",
                                     "headers": {"Host": d.get("host") or d.get("add")}}
        elif net == "grpc":
            stream["grpcSettings"] = {"serviceName": d.get("path") or ""}
        elif net != "tcp":
            return None
        if str(d.get("tls", "")).lower() == "tls":
            stream["security"] = "tls"
            stream["tlsSettings"] = {"serverName": d.get("sni") or d.get("host") or d.get("add"),
                                      "allowInsecure": True}
        try:
            return {
                "protocol": "vmess",
                "settings": {"vnext": [{"address": d["add"], "port": int(d["port"]), "users": [
                    {"id": d["id"], "alterId": int(d.get("aid") or 0), "security": d.get("scy") or "auto"}
                ]}]},
                "streamSettings": stream,
            }
        except Exception:
            return None

    if scheme not in ("vless", "trojan"):
        return None  # ss و بقیه فعلاً پشتیبانی نمی‌شن

    try:
        userinfo, hostpart = body.split("@", 1)
        main, _, query_str = hostpart.partition("?")
        host, port = main.rsplit(":", 1)
        host = host.strip("[]")
        port = int(port)
    except Exception:
        return None
    q = {k: v[0] for k, v in parse_qs(query_str).items()}

    net = q.get("type", "tcp")
    stream = {"network": net}
    if net == "ws":
        stream["wsSettings"] = {"path": q.get("path") or "/", "headers": {"Host": q.get("host") or ""}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": q.get("serviceName") or q.get("path") or ""}
    elif net != "tcp":
        return None  # xhttp/kcp/quic فعلاً پشتیبانی نمی‌شن

    security = q.get("security", "none")
    if security == "tls":
        stream["security"] = "tls"
        stream["tlsSettings"] = {"serverName": q.get("sni") or "", "allowInsecure": True,
                                  "fingerprint": q.get("fp") or "chrome"}
    elif security == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {"serverName": q.get("sni") or "", "publicKey": q.get("pbk") or "",
                                      "shortId": q.get("sid") or "", "fingerprint": q.get("fp") or "chrome"}
    elif security != "none":
        return None

    if scheme == "vless":
        user = {"id": userinfo, "encryption": q.get("encryption") or "none"}
        if q.get("flow"):
            user["flow"] = q["flow"]
        return {"protocol": "vless",
                "settings": {"vnext": [{"address": host, "port": port, "users": [user]}]},
                "streamSettings": stream}
    return {"protocol": "trojan",
            "settings": {"servers": [{"address": host, "port": port, "password": userinfo}]},
            "streamSettings": stream}


def xray_alive(cfg):
    """True اگه xray واقعاً بتونه از پشت این کانفیگ یه سایت رو باز کنه.
    None اگه پروتکلش پشتیبانی نشه (تا با تست پورت جایگزینش کنیم)."""
    outbound = cfg_to_xray_outbound(cfg)
    if outbound is None:
        return None
    port = _free_port()
    xray_cfg = {
        "log": {"loglevel": "none"},
        "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks",
                       "settings": {"udp": False}}],
        "outbounds": [outbound],
    }
    fd, path = tempfile.mkstemp(suffix=".json")
    proc = None
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(xray_cfg, f)
        proc = subprocess.Popen([XRAY_BIN, "run", "-c", path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.7)  # فرصت بالا اومدن xray
        if proc.poll() is not None:
            return False  # خود xray بالا نیومد (کانفیگ نامعتبره)
        r = requests.get(
            XRAY_TEST_URL,
            proxies={"http": f"socks5h://127.0.0.1:{port}", "https": f"socks5h://127.0.0.1:{port}"},
            timeout=XRAY_TIMEOUT,
        )
        return r.status_code < 400
    except Exception:
        return False
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
        try:
            os.remove(path)
        except OSError:
            pass


def pick(configs, seen):
    seen_set = set(seen)
    configs = [c for c in configs if get_flag(c) not in EXCLUDE]
    # اول کانفیگ‌های نفرستاده‌شده، بعد بقیه؛ داخل هر دسته تصادفی
    ordered = sorted(configs, key=lambda c: (h(c) in seen_set, random.random()))

    def port_alive(cands):
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
    xray_tested = 0
    rest = ordered
    while len(chosen) < TOTAL and rest:
        chunk, rest = rest[:20], rest[20:]
        for c in port_alive(chunk):
            if len(chosen) >= TOTAL:
                break
            if not XRAY_TEST or xray_tested >= XRAY_MAX_TEST:
                chosen.append(c)
                continue
            xray_tested += 1
            try:
                result = xray_alive(c)
            except Exception:
                result = False
            if result is not False:  # True یا None (پروتکل پشتیبانی‌نشده) قبول می‌شه
                chosen.append(c)
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
        chosen = chosen[:-1]


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
