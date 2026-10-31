#!/usr/bin/env python3
"""TerminalText: a small, whitelist-first email and phone SMS terminal UI."""
import curses
import email
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr
import imaplib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import smtplib
import subprocess
import sys
import textwrap
import time
from datetime import datetime

APP_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home()/".config")) / "terminaltext"
CONFIG = APP_DIR / "config.json"
DB = APP_DIR / "messages.json"
GREEN = "\033[38;5;28m"
MAX_EMAIL_TEXT = 500
MAX_SMS_TEXT = 480
POLL_SECONDS = 15

DEFAULT = {"contacts": [], "email": {}, "kdeconnect_device": "", "sms_source": "kdeconnect"}

def load_config():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(CONFIG.read_text())
        return {**DEFAULT, **data}
    except (OSError, ValueError):
        return dict(DEFAULT)

def save_config(cfg):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG)

def load_messages():
    try:
        return json.loads(DB.read_text())
    except (OSError, ValueError):
        return []

def save_messages(messages):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB.with_suffix(".tmp")
    tmp.write_text(json.dumps(messages[-500:], ensure_ascii=False, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(DB)

def decode_header_text(value):
    chunks = []
    for part, enc in decode_header(value or ""):
        if isinstance(part, bytes):
            chunks.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            chunks.append(part)
    return "".join(chunks)

def plain_body(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/plain" and not part.get_filename():
            try:
                payload = part.get_payload(decode=True)
                if payload is not None:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except (LookupError, UnicodeError):
                continue
    return "[No plain-text body]"

def contact_for(cfg, value, field):
    value = value.casefold()
    for c in cfg["contacts"]:
        if (c.get(field) or "").casefold() == value:
            return c
    return None

def poll_email(cfg, messages):
    e = cfg.get("email", {})
    required = ("imap_host", "address", "password")
    if not all(e.get(k) for k in required):
        return False
    try:
        box = imaplib.IMAP4_SSL(e["imap_host"], int(e.get("imap_port", 993)), timeout=8)
        box.login(e["address"], e["password"])
        box.select("INBOX", readonly=True)
        status, rows = box.search(None, "ALL")
        if status != "OK":
            box.logout()
            return False
        ids = rows[0].split()[-30:]
        existing = {m.get("id") for m in messages}
        changed = False
        for uid in reversed(ids):
            status, data = box.fetch(uid, "(BODY.PEEK[])")
            if status != "OK" or not data or not data[0]:
                continue
            raw = data[0][1]
            msg = email.message_from_bytes(raw)
            sender = parseaddr(msg.get("From", ""))[1]
            if not sender or not contact_for(cfg, sender, "email"):
                continue
            ident = "mail:" + str(msg.get("Message-ID") or uid.decode())
            if ident in existing:
                continue
            body = re.sub(r"\s+", " ", plain_body(msg)).strip()
            messages.append({"id": ident, "kind": "EMAIL", "from": sender,
                             "name": contact_for(cfg, sender, "email")["name"],
                             "subject": decode_header_text(msg.get("Subject"))[:100],
                             "body": body[:MAX_EMAIL_TEXT], "time": time.time(), "attachments": False})
            existing.add(ident)
            changed = True
        box.logout()
        if changed:
            save_messages(messages)
        return changed
    except Exception:
        return False

def poll_sms_notifications(cfg, messages):
    """Mirror short SMS notification text when kdeconnect-cli exposes it."""
    exe = shutil.which("kdeconnect-cli")
    if not exe:
        return False
    device = cfg.get("kdeconnect_device") or None
    cmd = [exe, "--list-notifications"]
    if device:
        cmd += ["--device", device]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    # KDE Connect notification text varies by desktop version. Only import lines
    # that clearly identify an SMS notification and whose phone number is allowed.
    existing = {m.get("id") for m in messages}
    changed = False
    for line in p.stdout.splitlines():
        if not re.match(r"\s*-\s*(sms|messages?)\s*:", line, re.I):
            continue
        number = re.search(r"\+\d{7,15}", line)
        c = contact_for(cfg, number.group(), "phone") if number else None
        if c is None:
            low = line.casefold()
            c = next((contact for contact in cfg["contacts"]
                      if contact.get("phone") and contact.get("name", "").casefold() in low), None)
        if not c:
            continue
        ident = "sms-notif:" + hashlib.sha256(line.encode("utf-8", "replace")).hexdigest()
        if ident in existing:
            continue
        messages.append({"id": ident, "kind": "SMS", "from": number.group(), "name": c["name"],
                         "subject": "", "body": line[:MAX_SMS_TEXT], "time": time.time(), "attachments": False})
        changed = True
    if changed:
        save_messages(messages)
    return changed

def send_email(cfg, c, subject, body):
    e = cfg.get("email", {})
    required = ("smtp_host", "address", "password")
    if not all(e.get(k) for k in required):
        raise RuntimeError("Set up email first with option 5")
    msg = EmailMessage()
    msg["From"] = e["address"]
    msg["To"] = c["email"]
    msg["Subject"] = subject[:120]
    msg.set_content(body[:MAX_EMAIL_TEXT])
    with smtplib.SMTP_SSL(e["smtp_host"], int(e.get("smtp_port", 465)), timeout=12) as server:
        server.login(e["address"], e["password"])
        server.send_message(msg)

def send_sms(cfg, c, body):
    exe = shutil.which("kdeconnect-cli")
    if not exe:
        raise RuntimeError("KDE Connect desktop is not installed")
    cmd = [exe]
    if cfg.get("kdeconnect_device"):
        cmd += ["--device", cfg["kdeconnect_device"]]
    cmd += ["--send-sms", body[:MAX_SMS_TEXT], "--destination", c["phone"]]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or "KDE Connect could not send SMS").strip()[:250])

def add_contact(cfg, ask):
    name = ask("Name (blank cancels)")
    if not name:
        return "Cancelled"
    email_addr = ask("Email (optional)").strip()
    phone = ask("Phone in international format, e.g. +14155550123 (optional)").strip()
    if not email_addr and not phone:
        return "Add an email address or phone number"
    if email_addr and ("@" not in email_addr or " " in email_addr):
        return "That email address does not look valid"
    if phone and not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
        return "Phone needs + and country code, for example +14155550123"
    for c in cfg["contacts"]:
        if email_addr and c.get("email", "").casefold() == email_addr.casefold():
            return "That email is already whitelisted"
        if phone and c.get("phone") == phone:
            return "That phone number is already whitelisted"
    cfg["contacts"].append({"name": name.strip()[:60], "email": email_addr, "phone": phone})
    save_config(cfg)
    return f"Added {name.strip()} to the whitelist"

def setup_email(cfg, ask, message):
    e = dict(cfg.get("email", {}))
    e["address"] = ask("Email address").strip()
    e["password"] = ask("Email app password (stored locally with owner-only permissions)", secret=True)
    e["imap_host"] = ask("IMAP host (example: imap.gmail.com)").strip()
    e["imap_port"] = ask("IMAP TLS port [993]").strip() or "993"
    e["smtp_host"] = ask("SMTP host (example: smtp.gmail.com)").strip()
    e["smtp_port"] = ask("SMTP TLS port [465]").strip() or "465"
    if not all(e.get(k) for k in ("address", "password", "imap_host", "smtp_host")):
        return "Email setup cancelled: all fields are required"
    cfg["email"] = e
    save_config(cfg)
    return "Email settings saved. Add approved email contacts before messages appear."

def draw(stdscr, cfg, messages, selected, status):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    if h < 12 or w < 60:
        stdscr.addstr(0, 0, "Please resize terminal to at least 60x12", curses.color_pair(1))
        stdscr.refresh(); return
    sep = max(26, min(36, w//3))
    def put(y, x, s, attr=0, width=None):
        if y >= h or x >= w: return
        s = str(s).replace("\n", " ")
        if width is not None: s = s[:width]
        try: stdscr.addnstr(y, x, s, max(0, w-x-1), attr)
        except curses.error: pass
    attr = curses.color_pair(1)
    put(0, 1, "TERMINALTEXT", attr|curses.A_BOLD)
    put(1, 1, "WHITELISTED MESSAGES", attr)
    put(2, 1, "─"*max(1, w-2), attr)
    for y, line in enumerate(["1  add contact", "2  email contact", "3  text contact", "4  call contact", "5  email setup", "6  refresh now", "q  quit", "", "Type ... to quit"], 3):
        put(y, 2, line, attr)
    put(14, 2, f"Contacts: {len(cfg['contacts'])}", attr)
    e = cfg.get("email", {})
    put(15, 2, "Email: configured" if e.get("address") else "Email: not set up", attr)
    put(16, 2, f"Poll: {POLL_SECONDS}s | refresh: r", attr)
    put(18, 1, (status or "Ready")[:sep-2], attr)
    for y in range(2, h-1):
        put(y, sep, "│", attr)
    put(1, sep+2, "LATEST FIRST", attr|curses.A_BOLD)
    sorted_messages = sorted(messages, key=lambda m:m.get("time", 0), reverse=True)
    available = max(0, h-5)
    if not sorted_messages:
        put(4, sep+2, "No whitelisted messages yet.", attr)
        put(5, sep+2, "Add contacts, then set up email or pair KDE Connect.", attr)
    for i, m in enumerate(sorted_messages[:available]):
        y = 3 + i
        t = datetime.fromtimestamp(m.get("time", time.time())).strftime("%H:%M")
        title = f"{m.get('kind','?')} {t} {m.get('name') or m.get('from','')}: {m.get('subject','')}"
        put(y, sep+2, title[:w-sep-4], attr| (curses.A_BOLD if i == selected else 0))
        body_lines = textwrap.wrap(m.get("body", "").replace("\r", " "), max(15, w-sep-6))
        if body_lines and y+1 < h-1:
            put(y+1, sep+4, body_lines[0][:w-sep-6], attr)
        if len(body_lines)>1 and y+2<h-1:
            put(y+2, sep+4, body_lines[1][:w-sep-6], attr)
        if y+3 < h-1: put(y+3, sep+2, "-"*max(1,w-sep-4), attr)
    if status:
        put(h-2, 1, status[:w-2], attr)
    stdscr.refresh()

def loading(stdscr):
    start = time.monotonic()
    curses.curs_set(0)
    h,w=stdscr.getmaxyx(); y=max(0,h//2-1); x=max(0,w//2-3)
    stdscr.erase(); stdscr.addstr(y,x,"loading",curses.color_pair(1)); stdscr.refresh()
    # Ear twitch then single wink within the requested 1.19 s window.
    cat_a = [r" /\_/\ ", "( o.o )", " > ^ < "]
    cat_b = [r" /\_/\ ", "( -.o )", " > ^ < "]
    cat_c = [r" /\_/\ ", "( o.o )", " > ^ < "]
    for frame, cat in enumerate((cat_a, cat_b, cat_c)):
        xx=max(0,w//2-4); yy=min(h-4,y+2)
        try:
            for j,line in enumerate(cat): stdscr.addstr(yy+j,xx,line,curses.color_pair(1))
            stdscr.refresh()
        except curses.error: pass
        target = start + (0.28, 0.78, 1.19)[frame]
        time.sleep(max(0, target-time.monotonic()))
    time.sleep(max(0, 1.19-(time.monotonic()-start)))

def run(stdscr):
    cfg=load_config(); messages=load_messages(); status=""
    curses.start_color(); curses.use_default_colors(); curses.init_pair(1, 22, -1)
    curses.curs_set(0); stdscr.timeout(250)
    loading(stdscr)
    last_poll=0; selected=0
    def ask(prompt, secret=False):
        curses.curs_set(1)
        h,w=stdscr.getmaxyx(); y=h-2
        stdscr.move(y,0); stdscr.clrtoeol(); stdscr.addstr(y,0,prompt[:w-2],curses.color_pair(1)); stdscr.refresh()
        curses.echo(not secret)
        try: raw=stdscr.getstr(y, min(w-2,len(prompt)), max(1,w-min(w-4,len(prompt))-1))
        finally:
            curses.noecho(); curses.curs_set(0)
        return raw.decode("utf-8",errors="replace").strip()
    while True:
        now=time.monotonic()
        if now-last_poll>=POLL_SECONDS:
            poll_email(cfg,messages); poll_sms_notifications(cfg,messages); last_poll=now
        draw(stdscr,cfg,messages,selected,status)
        status=""
        key=stdscr.getch()
        if key==-1: continue
        if key in (ord('q'), 27): break
        if key==ord('r') or key==ord('6'):
            poll_email(cfg,messages); poll_sms_notifications(cfg,messages); last_poll=time.monotonic(); status="Refreshed"
        elif key==ord('1'):
            status=add_contact(cfg,ask)
        elif key==ord('5'):
            status=setup_email(cfg,ask,status)
        elif key in (curses.KEY_UP, curses.KEY_DOWN):
            selected=max(0,selected+(1 if key==curses.KEY_DOWN else -1))
        elif key==ord('2') or key==ord('3') or key==ord('4'):
            channel={ord('2'):"email",ord('3'):"phone",ord('4'):"phone"}[key]
            options=[c for c in cfg["contacts"] if c.get(channel)]
            if not options:
                status="No whitelisted contact has that address yet"
                continue
            for i,c in enumerate(options,1):
                address=c.get(channel,"(none)")
                stdscr.addstr(i-1,0,f"{i}. {c['name']}  {address}"[:max(1,stdscr.getmaxyx()[1]-1)],curses.color_pair(1))
            choice=ask("Contact number (blank cancels)")
            if not choice: continue
            if not choice.isdigit() or not (1<=int(choice)<=len(options)):
                status="Invalid contact choice"; continue
            c=options[int(choice)-1]
            if key==ord('4'):
                try:
                    if not shutil.which("xdg-open"): raise RuntimeError("Install xdg-utils to open a call handler")
                    subprocess.Popen(["xdg-open","tel:"+c["phone"]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                    status=f"Opened call handler for {c['name']}"
                except Exception as ex: status=str(ex)
                continue
            if key==ord('2'):
                subject=ask("Subject (blank cancels)")
                if not subject: continue
                body=ask(f"Email text (up to {MAX_EMAIL_TEXT} characters)")
            else:
                body=ask(f"SMS text (up to {MAX_SMS_TEXT} characters; blank cancels)")
            if not body: continue
            try:
                if key==ord('2'):
                    send_email(cfg,c,subject,body)
                    messages.append({"id":"sent:"+str(time.time_ns()),"kind":"EMAIL OUT","from":cfg["email"]["address"],"name":c["name"],"subject":subject,"body":body[:MAX_EMAIL_TEXT],"time":time.time(),"attachments":False})
                else:
                    send_sms(cfg,c,body)
                    messages.append({"id":"sent:"+str(time.time_ns()),"kind":"SMS OUT","from":"you","name":c["name"],"subject":"","body":body[:MAX_SMS_TEXT],"time":time.time(),"attachments":False})
                save_messages(messages); status=f"Sent to {c['name']}"
            except Exception as ex: status=f"Send failed: {str(ex)[:150]}"
        elif key==ord('.'):
            nxt=stdscr.getch()
            if nxt==ord('.') and stdscr.getch()==ord('.'): break
            status="Use ... then Enter to quit" # command form documented in prompt
        elif key==ord(':'):
            cmd=ask("Command (type ... to quit)")
            if cmd=="...": break
            status="Unknown command" if cmd else ""

def main():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("TerminalText needs an interactive terminal.", file=sys.stderr); return 2
    try: curses.wrapper(run)
    except KeyboardInterrupt: pass
    return 0

if __name__=="__main__":
    raise SystemExit(main())
