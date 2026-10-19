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
from urllib.parse import parse_qs, quote, unquote

import requests

# ================== تنظیمات ==================
TOKEN = os.environ.get("BOT_TOKEN")
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

TOTAL = 5
EXCLUDE = {"🇮🇷"}
REBRAND = True
PING_TEST = True
PING_NODES = ["ir1.node.check-host.net", "ir3.node.check-host.net", "ir6.node.check-host.net"]
PING_MIN_OK = 2
PING_TIMEOUT = 15
XRAY_TEST = True
XRAY_BIN = "./xray"
XRAY_TIMEOUT = 5          # ۵ ثانیه مهلت پاسخ از آدرس سبک تست
XRAY_TEST_URL = "https://www.gstatic.com/generate_204"
DEBUG = True
XRAY_MAX_TEST = 80        # حداکثر تعداد تست با xray در هر نوبت
MAX_POST_AGE = 12 * 3600  # سقف زمانی نیم‌روز (۱۲ ساعت) برای اعتبار پست قبلی
MIN_PREV_ALIVE = 3        # حداقل ۳ کانفیگ از ۵ کانفیگ قبلی باید سالم باشند
USE_QUOTE = False
SEEN_FILE = "seen.json"
STATE_FILE = "last_post.json"
MAX_SEEN = 500
# ============================================

CFG_RE = re.compile(r"\b(?:vmess|vless|trojan|ss|hysteria2|hy2|tuic)://[^\r\n<>\"'\s]+")
FLAG_RE = re.compile(r"[\U0001F1E6-\U0001F1FF]{2}")
TAG_RE = re.compile(r"@[A-Za-z0-9_]{4,}")
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001F1E5\U0001F200-\U0001FAFF\u2190-\u21FF\u2300-\u23FF"
    "\u2600-\u27BF\u2B00-\u2BFF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3]"
)
HP_RE = re.compile(r"@(\[[^\]]+\]|[^@/?#:\s]+):(\d+)")
UDP_RE = re.compile(r"^(?:hysteria2|hy2|tuic)://|[?&]type=(?:kcp|quic)\b")

# حافظه موقت برای جلوگیری از تست تکراری یک آی‌پی و پورت در check-host.net
PING_CACHE = {}


def b64d(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-MAX_SEEN:], f)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def h(cfg):
    body = cfg.split("#", 1)[0]
    return hashlib.md5(body.encode()).hexdigest()


def fetch_configs():
    found_all = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    for ch in SOURCE_CHANNELS:
        url = f"https://t.me/s/{ch}"
        try:
            r = requests.get(url, timeout=20, headers=headers)
            if not r.ok:
                continue
            text = html.unescape(r.text)
            found = [c.strip() for c in CFG_RE.findall(text) if c.strip()]
            found.reverse()
            if DEBUG:
                print(f"[دیباگ] کانال @{ch}: تعداد {len(found)} کانفیگ پیدا شد")
            found_all.extend(found)
        except Exception as e:
            if DEBUG:
                print(f"[دیباگ] خطا در دریافت کانال {ch}: {e}")
            continue
    unique = list(dict.fromkeys(found_all))
    if DEBUG:
        print(f"[دیباگ] مجموع کانفیگ‌های یکتا: {len(unique)}")
    return unique


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


def get_host_port(cfg):
    scheme, rest = cfg.split("://", 1)
    body = rest.split("#", 1)[0]
    if scheme == "vmess":
        try:
            d = json.loads(b64d(body))
            return str(d["add"]).strip(), int(d["port"])
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


def is_iran_host(cfg):
    if get_flag(cfg) in EXCLUDE:
        return True
    hp = get_host_port(cfg)
    if hp and hp[0].lower().endswith(".ir"):
        return True
    return False


def tcp_ping(cfg):
    if UDP_RE.search(cfg):
        return False
    hp = get_host_port(cfg)
    if not hp:
        return False
    if hp in PING_CACHE:
        return PING_CACHE[hp]

    host, port = hp
    time.sleep(1.0)  # مکث کوتاه برای جلوگیری از مسدود شدن توسط check-host.net
    try:
        r = requests.get(
            "https://check-host.net/check-tcp",
            params={"host": f"{host}:{port}", "node": PING_NODES},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        req_id = r.json().get("request_id")
        if not req_id:
            if DEBUG:
                print(f"[دیباگ] check-host برای {hp} شناسه نداد: {r.text[:200]}")
            return False
    except Exception as e:
        if DEBUG:
            print(f"[دیباگ] خطای ارتباط با check-host برای {hp}: {e}")
        return False

    deadline = time.time() + PING_TIMEOUT
    while time.time() < deadline:
        time.sleep(1.5)
        try:
            res = requests.get(
                f"https://check-host.net/check-result/{req_id}",
                headers={"Accept": "application/json"},
                timeout=10,
            ).json()
        except Exception:
            continue
        ok_count = 0
        pending = False
        for node_result in res.values():
            if not node_result:
                pending = True
                continue
            if any(isinstance(e, dict) and "time" in e for e in node_result):
                ok_count += 1
        if ok_count >= PING_MIN_OK:
            PING_CACHE[hp] = True
            if DEBUG:
                print(f"[دیباگ] {hp}: پینگ از ایران موفق ({ok_count} نود)")
            return True
        if not pending:
            break

    PING_CACHE[hp] = False
    if DEBUG:
        print(f"[دیباگ] {hp}: پینگ از نودهای ایران رد شد")
    return False


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def cfg_to_xray_outbound(cfg):
    scheme, rest = cfg.split("://", 1)
    body = rest.split("#", 1)[0]

    if scheme == "vmess":
        try:
            d = json.loads(b64d(body))
        except Exception:
            return None
        address = str(d.get("add") or "").strip()
        if not address:
            return None
        net = d.get("net") or "tcp"
        host_hdr = d.get("host") or address
        path_val = d.get("path") or "/"
        stream = {"network": net}
        if net == "ws":
            stream["wsSettings"] = {"path": path_val, "headers": {"Host": host_hdr}}
        elif net == "grpc":
            stream["grpcSettings"] = {"serviceName": d.get("path") or ""}
        elif net == "httpupgrade":
            stream["httpupgradeSettings"] = {"path": path_val, "host": host_hdr}
        elif net != "tcp":
            return None
        if str(d.get("tls", "")).lower() == "tls":
            stream["security"] = "tls"
            tls_cfg = {
                "serverName": d.get("sni") or host_hdr or address,
                "fingerprint": d.get("fp") or "chrome",
            }
            if d.get("alpn"):
                tls_cfg["alpn"] = [a.strip() for a in str(d["alpn"]).split(",") if a.strip()]
            stream["tlsSettings"] = tls_cfg
        try:
            return {
                "protocol": "vmess",
                "settings": {
                    "vnext": [
                        {
                            "address": address,
                            "port": int(d["port"]),
                            "users": [
                                {
                                    "id": d["id"],
                                    "alterId": int(d.get("aid") or 0),
                                    "security": d.get("scy") or "auto",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": stream,
            }
        except Exception:
            return None

    if scheme not in ("vless", "trojan"):
        return None

    try:
        userinfo, hostpart = body.split("@", 1)
        userinfo = unquote(userinfo)
        main, _, query_str = hostpart.partition("?")
        host, port = main.rsplit(":", 1)
        host = host.strip("[]")
        port = int(port)
    except Exception:
        return None
    q = {k: v[0] for k, v in parse_qs(query_str).items()}

    net = (q.get("type") or "tcp").lower()
    host_hdr = q.get("host") or host
    path_val = unquote(q.get("path") or "/")
    stream = {"network": net}
    if net == "ws":
        stream["wsSettings"] = {"path": path_val, "headers": {"Host": host_hdr}}
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": q.get("serviceName") or q.get("path") or ""}
    elif net == "httpupgrade":
        stream["httpupgradeSettings"] = {"path": path_val, "host": host_hdr}
    elif net != "tcp":
        return None

    security = (q.get("security") or "none").lower()
    if security == "tls":
        stream["security"] = "tls"
        tls_cfg = {
            "serverName": q.get("sni") or host_hdr or host,
            "fingerprint": q.get("fp") or "chrome",
        }
        if q.get("alpn"):
            tls_cfg["alpn"] = [a.strip() for a in q["alpn"].split(",") if a.strip()]
        stream["tlsSettings"] = tls_cfg
    elif security == "reality":
        if not q.get("pbk"):
            return None
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": q.get("sni") or host_hdr or host,
            "publicKey": q.get("pbk") or "",
            "shortId": q.get("sid") or "",
            "fingerprint": q.get("fp") or "chrome",
        }
    elif security != "none":
        return None

    if scheme == "vless":
        user = {"id": userinfo, "encryption": q.get("encryption") or "none"}
        if q.get("flow"):
            user["flow"] = q["flow"]
        return {
            "protocol": "vless",
            "settings": {"vnext": [{"address": host, "port": port, "users": [user]}]},
            "streamSettings": stream,
        }
    return {
        "protocol": "trojan",
        "settings": {"servers": [{"address": host, "port": port, "password": userinfo}]},
        "streamSettings": stream,
    }


def xray_alive(cfg):
    outbound = cfg_to_xray_outbound(cfg)
    if outbound is None:
        return None
    tag = get_host_port(cfg)
    port = _free_port()
    xray_cfg = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "socks",
                "settings": {"udp": False},
            }
        ],
        "outbounds": [outbound],
    }
    fd, path = tempfile.mkstemp(suffix=".json")
    proc = None
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(xray_cfg, f)
        proc = subprocess.Popen(
            [XRAY_BIN, "run", "-c", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(0.6)
        if proc.poll() is not None:
            if DEBUG:
                print(f"[دیباگ] {tag}: خود xray بالا نیومد -> {proc.stdout.read()[:300]}")
            return False
        r = requests.get(
            XRAY_TEST_URL,
            proxies={"http": f"socks5h://127.0.0.1:{port}", "https": f"socks5h://127.0.0.1:{port}"},
            timeout=XRAY_TIMEOUT,
        )
        if DEBUG:
            print(f"[دیباگ] {tag}: تست xray موفق (کد {r.status_code})")
        return r.status_code < 400
    except Exception as e:
        if DEBUG:
            print(f"[دیباگ] {tag}: خطا در xray -> {type(e).__name__}")
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


def is_full_alive(cfg):
    """اول تست سریع xray، و فقط در صورت موفقیت، تست پینگ از نودهای ایران."""
    if XRAY_TEST and xray_alive(cfg) is not True:
        return False
    if PING_TEST and not tcp_ping(cfg):
        return False
    return True


def pick(configs, seen):
    seen_set = set(seen)
    valid_configs = [
        c for c in configs
        if not is_iran_host(c) and cfg_to_xray_outbound(c) is not None
    ]
    if DEBUG:
        print(f"[دیباگ] تعداد کانفیگ‌های قابل تست با xray: {len(valid_configs)}")

    ordered = sorted(valid_configs, key=lambda c: (h(c) in seen_set, random.random()))

    chosen = []
    xray_tested = 0
    for c in ordered:
        if len(chosen) >= TOTAL:
            break
        if XRAY_TEST and xray_tested >= XRAY_MAX_TEST:
            break
        xray_tested += 1

        if is_full_alive(c):
            chosen.append(c)
            if DEBUG:
                print(f"[دیباگ] ✓ کانفیگ انتخاب شد ({len(chosen)}/{TOTAL})")

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


def send(msg, reply_to_message_id=None):
    payload = {
        "chat_id": CHANNEL,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to_message_id:
        payload["reply_parameters"] = json.dumps({"message_id": int(reply_to_message_id)})

    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data=payload,
        timeout=30,
    )
    if not r.ok:
        print("Telegram error:", r.text)
        return None
    return r.json().get("result", {}).get("message_id")


def run_once():
    state = load_state()
    last_msg_id = state.get("message_id")
    last_configs = state.get("configs", [])
    last_ts = state.get("timestamp", 0)
    now = int(time.time())

    if last_msg_id and last_configs and last_ts and (now - last_ts < MAX_POST_AGE):
        if DEBUG:
            age_hours = round((now - last_ts) / 3600, 1)
            print(f"[دیباگ] از پست قبلی {age_hours} ساعت گذشته است؛ شروع تست سلامت...")
        alive_count = 0
        for idx, prev_cfg in enumerate(last_configs):
            if is_full_alive(prev_cfg):
                alive_count += 1
            remaining = len(last_configs) - (idx + 1)
            if alive_count + remaining < MIN_PREV_ALIVE:
                break
            if alive_count >= MIN_PREV_ALIVE:
                break

        if alive_count >= MIN_PREV_ALIVE:
            send("✅ متصل", reply_to_message_id=last_msg_id)
            return f"پست قبلی هنوز فعال است (حداقل {alive_count} کانفیگ سالم)؛ ریپلای «متصل» ارسال شد"
        else:
            if DEBUG:
                print(f"[دیباگ] فقط {alive_count} کانفیگ از پست قبلی سالم بود؛ ارسال پست جدید...")
    else:
        if DEBUG and last_msg_id:
            print("[دیباگ] سقف ۱۲ ساعت پست قبلی تمام شده است؛ ارسال کانفیگ‌های جدید...")

    configs = fetch_configs()
    if not configs:
        return "هیچ کانفیگی پیدا نشد"
    seen = load_seen()
    chosen = pick(configs, seen)
    if not chosen:
        return "هیچ کانفیگ زنده‌ای پیدا نشد"
    msg_id = send(build_message(chosen))
    if msg_id:
        seen += [h(c) for c in chosen]
        save_seen(seen)
        save_state({"message_id": msg_id, "configs": chosen, "timestamp": int(time.time())})
        return f"{len(chosen)} کانفیگ جدید ارسال شد"
    return "ارسال به تلگرام ناموفق بود"


if __name__ == "__main__":
    print(run_once())
