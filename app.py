"""
WEALTH-TECH Key Bot - force-join gate + admin-set key + hidden admin panel
pip install "python-telegram-bot>=22"
python keybot.py

SETUP:
1. Put the 2 admin usernames in ADMINS (no @).
2. Add the bot as ADMIN in every channel it must verify.
3. /admin is hidden: never add it via BotFather /setcommands.
"""
import asyncio
import html as H
import json
import logging
import os
import re
import threading

from telegram import CopyTextButton, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
TOKEN = "8931275507:AAHYO7C79ua0wfcYdKoNp1kD6EAAlViJdhw"  # TEST ONLY - revoke before sharing

ADMINS = {"wealthtechmod", "Pythonx100"}  # usernames WITHOUT @, case-insensitive

# ---------- storage (plain JSON file, works anywhere) ----------
DATA_FILE = os.environ.get("DATA_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
_lock = threading.Lock()


def _load():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("channels", [])
    d.setdefault("settings", {})
    d.setdefault("next_id", 1)
    return d


DATA = _load()


def _save():
    tmp = DATA_FILE + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(DATA, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)  # atomic: never leaves a half-written file


def get_channels():
    # Labels are auto-numbered by position, so removing one renumbers the rest
    return [(c["id"], f"Channel {i}", c["chat"], c["url"]) for i, c in enumerate(DATA["channels"], 1)]


def add_channel(chat, url):
    chat = str(chat)
    if any(c["chat"].lower() == chat.lower() for c in DATA["channels"]):
        return False
    DATA["channels"].append({"id": DATA["next_id"], "chat": chat, "url": url})
    DATA["next_id"] += 1
    _save()
    return True


def delete_channel(cid):
    DATA["channels"] = [c for c in DATA["channels"] if c["id"] != cid]
    _save()


def update_channel(cid, url, chat=None):
    for c in DATA["channels"]:
        if c["id"] == cid:
            c["url"] = url
            if chat:
                c["chat"] = chat
    _save()


def get_setting(k, default=None):
    return DATA["settings"].get(k, default)


def set_setting(k, v):
    if v is None:
        DATA["settings"].pop(k, None)
    else:
        DATA["settings"][k] = v
    _save()


def _migrate_old_sqlite():
    """One-time import from the previous keys.db, if it exists next to data.json."""
    path = os.path.join(os.path.dirname(DATA_FILE), "keys.db")
    if DATA["channels"] or DATA["settings"] or not os.path.exists(path):
        return
    try:
        import sqlite3

        c = sqlite3.connect(path)
        for chat, url in c.execute("SELECT chat, url FROM channels ORDER BY id"):
            add_channel(chat, url)
        for k, v in c.execute("SELECT k, v FROM settings"):
            DATA["settings"][k] = v
        c.close()
        _save()
    except Exception as e:  # noqa
        logging.warning("sqlite import skipped: %s", e)


_migrate_old_sqlite()


def is_admin(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.username and u.username.lower() in {a.lower() for a in ADMINS})


# ---------- user UI ----------
def styled(text, style, **kw):
    # "style" colours buttons on recent Bot API (danger=red, success=green, primary=blue)
    return Btn(text, api_kwargs={"style": style}, **kw)


def main_kb() -> InlineKeyboardMarkup:
    rows = []
    folder = get_setting("folder_url")
    if folder:
        rows.append([styled("📁 JOIN ALL CHANNELS • 1 CLICK ✨", "danger", url=folder)])
    if not folder or get_setting("show_btns", "1") == "1":
        pair = []
        for _, label, _, url in get_channels():
            pair.append(styled(f"🔔 {label}", "danger", url=url))
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
    rows.append([styled("🌟 GENERATE KEY", "success", callback_data="gen")])
    rows.append([styled("🔗 VERIFY JOINED", "primary", callback_data="verify")])
    return InlineKeyboardMarkup(rows)


WELCOME = (
    "✨💎 <b>WELCOME TO WEALTH-TECH KEYS</b> 💎✨\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "🚫 <b>Join all channels to unlock</b> 🔓\n\n"
    "1️⃣ Tap the join button(s) 🔔\n"
    "2️⃣ Press <b>VERIFY JOINED</b> ✅\n"
    "3️⃣ Press <b>GENERATE KEY</b> 🌟\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "⚡ <i>Fast • Secure • Free</i> ⚡"
)


async def is_joined(bot, uid: int):
    missing = []
    for _, label, chat, _ in get_channels():
        try:
            cid = int(chat) if str(chat).lstrip('-').isdigit() else chat
            m = await bot.get_chat_member(cid, uid)
            if m.status not in ("member", "administrator", "creator"):
                missing.append(label)
        except TelegramError as e:
            logging.warning("check failed for %s (%s): %s", label, chat, e)
            missing.append(label)
    return missing


async def edit(q, text, kb=None):
    msg = q.message
    if msg.photo or msg.video or msg.animation:
        await q.edit_message_caption(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat.id
    kb = main_kb()
    kind, fid = get_setting("banner_kind"), get_setting("banner_id")
    kw = dict(caption=WELCOME, parse_mode=ParseMode.HTML, reply_markup=kb)
    try:
        if kind == "video" and fid:
            await ctx.bot.send_video(chat, fid, **kw)
        elif kind == "animation" and fid:
            await ctx.bot.send_animation(chat, fid, **kw)
        elif kind == "photo" and fid:
            await ctx.bot.send_photo(chat, fid, **kw)
        else:
            await ctx.bot.send_message(chat, WELCOME, parse_mode=ParseMode.HTML, reply_markup=kb)
    except TelegramError:
        await ctx.bot.send_message(chat, WELCOME, parse_mode=ParseMode.HTML, reply_markup=kb)


async def verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    missing = await is_joined(ctx.bot, q.from_user.id)
    if missing:
        shown = ", ".join(missing[:5]) + (f" +{len(missing) - 5} more" if len(missing) > 5 else "")
        await q.answer("❌ Still missing: " + shown, show_alert=True)
    else:
        await q.answer("✅ All channels joined! Tap GENERATE KEY 🌟", show_alert=True)


async def generate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if await is_joined(ctx.bot, q.from_user.id):
        await q.answer("🔒 Join all channels first!", show_alert=True)
        return
    key = get_setting("key")
    if not key:
        await q.answer("⏳ No key available yet. Check back soon!", show_alert=True)
        return
    await q.answer()

    for frame in ("✨ Generating key", "💫 Generating key.", "🌟 Generating key..", "⚡ Generating key..."):
        try:
            await edit(q, f"<b>{frame}</b>")
        except TelegramError:
            pass
        await asyncio.sleep(0.6)

    await edit(
        q,
        "🎉✨ <b>KEY GENERATED!</b> ✨🎉\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔑 <code>{H.escape(key)}</code>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "👆 <i>Tap the key or the copy button</i>\n"
        "💎 <b>WEALTH-TECH</b> 💎",
        InlineKeyboardMarkup(
            [
                [styled("📋 COPY KEY", "success", copy_text=CopyTextButton(text=key[:256]))],
                [Btn("🔄 Back to menu", callback_data="menu")],
            ]
        ),
    )


async def menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await edit(q, WELCOME, main_kb())


# ---------- hidden admin panel ----------
def panel_kb():
    on = get_setting("show_btns", "1") == "1"
    return InlineKeyboardMarkup(
        [
            [Btn("🎬 Set banner", callback_data="adm:banner"), Btn("🔑 Set key", callback_data="adm:key")],
            [Btn("📁 Set folder link", callback_data="adm:folder")],
            [Btn("➕ Add channel(s)", callback_data="adm:add"), Btn("✏️ Edit link", callback_data="adm:edit")],
            [Btn("🗑 Remove channel", callback_data="adm:del"), Btn("📋 List", callback_data="adm:list")],
            [Btn(f"👁 Channel buttons: {'ON' if on else 'OFF'}", callback_data="adm:toggle")],
            [Btn("❌ Close", callback_data="adm:close")],
        ]
    )


def panel_text():
    key = get_setting("key")
    folder = get_setting("folder_url")
    return (
        "🛠 <b>ADMIN PANEL</b>\n━━━━━━━━━━━━━━\n"
        f"🎬 Banner: {'✅ set' if get_setting('banner_id') else '❌ none'}\n"
        f"🔑 Key: {('<code>' + H.escape(key) + '</code>') if key else '❌ none'}\n"
        f"📁 Folder: {'✅ set' if folder else '❌ none'}\n"
        f"📢 Verified channels: {len(get_channels())}"
    )


async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # Silent for everyone who is not an admin
    if not is_admin(update) or update.effective_chat.type != "private":
        return
    ctx.user_data.pop("state", None)
    await update.message.reply_text(panel_text(), parse_mode=ParseMode.HTML, reply_markup=panel_kb())


def channel_pick_kb(prefix):
    rows = [[Btn(f"{label}  ({chat})", callback_data=f"adm:{prefix}:{cid}")] for cid, label, chat, _ in get_channels()]
    rows.append([Btn("⬅️ Back", callback_data="adm:home")])
    return InlineKeyboardMarkup(rows)


BACK = InlineKeyboardMarkup([[Btn("⬅️ Back", callback_data="adm:home")]])


async def admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(update):
        return
    parts = q.data.split(":")
    act = parts[1]
    h = ParseMode.HTML

    if act == "home":
        ctx.user_data.pop("state", None)
        await q.edit_message_text(panel_text(), parse_mode=h, reply_markup=panel_kb())
    elif act == "close":
        ctx.user_data.pop("state", None)
        await q.message.delete()
    elif act == "toggle":
        set_setting("show_btns", "0" if get_setting("show_btns", "1") == "1" else "1")
        await q.edit_message_text(panel_text(), parse_mode=h, reply_markup=panel_kb())
    elif act == "banner":
        ctx.user_data["state"] = "banner"
        await q.edit_message_text("🎬 Send the new <b>video / GIF / photo</b> now.", parse_mode=h, reply_markup=BACK)
    elif act == "key":
        ctx.user_data["state"] = "key"
        await q.edit_message_text(
            "🔑 Send the key users will receive.\nSend <code>-</code> to clear it.", parse_mode=h, reply_markup=BACK
        )
    elif act == "folder":
        ctx.user_data["state"] = "folder"
        await q.edit_message_text(
            "📁 Send your Telegram folder link:\n<code>https://t.me/addlist/XXXXXXXX</code>\n\n"
            "Send <code>-</code> to remove it.\n\n"
            "ℹ️ The bot can't read folder contents, so add the folder's channels with "
            "<b>Add channel(s)</b> to have them verified.",
            parse_mode=h,
            reply_markup=BACK,
        )
    elif act == "add":
        ctx.user_data["state"] = "add"
        await q.edit_message_text(
            "➕ Send the channel link(s), one per line:\n\n"
            "<code>https://t.me/mychannel</code>\n"
            "<code>https://t.me/+AbCdEf123</code> (private)\n\n"
            "Channels are numbered automatically.\n"
            "For a private link I'll ask you to forward one post from that channel "
            "(needed to verify members).",
            parse_mode=h,
            reply_markup=BACK,
        )
    elif act == "edit":
        await q.edit_message_text("✏️ Pick a channel to edit:", reply_markup=channel_pick_kb("editid"))
    elif act == "editid":
        ctx.user_data["state"] = f"edit:{int(parts[2])}"
        await q.edit_message_text(
            "✏️ Send the new link:\n<code>https://t.me/newlink</code>",
            parse_mode=h,
            reply_markup=BACK,
        )
    elif act == "del":
        await q.edit_message_text("🗑 Pick a channel to remove:", reply_markup=channel_pick_kb("delid"))
    elif act == "delid":
        delete_channel(int(parts[2]))
        await q.edit_message_text("✅ Removed. Remove another?", reply_markup=channel_pick_kb("delid"))
    elif act == "list":
        lines = [f"{i}. {l} - {c}\n   {u}" for i, (_, l, c, u) in enumerate(get_channels(), 1)]
        text = "📋 Channels:\n\n" + ("\n".join(lines) or "none")
        await q.edit_message_text(text[:4000], reply_markup=BACK, disable_web_page_preview=True)


def parse_link(text):
    """-> ('public', '@name', url) | ('private', None, url) | ('folder', None, url) | None"""
    t = text.strip()
    m = re.fullmatch(r"@([A-Za-z][A-Za-z0-9_]{3,})", t)
    if m:
        return "public", "@" + m.group(1), f"https://t.me/{m.group(1)}"
    m = re.fullmatch(r"(?:https?://)?(?:t|telegram)\.me/(.+)", t, re.I)
    if not m:
        return None
    parts = m.group(1).split("?")[0].strip("/").split("/")
    first = parts[0]
    if first.lower() == "addlist":
        return "folder", None, "https://t.me/" + "/".join(parts)
    if first.startswith("+") and len(first) > 1:
        return "private", None, "https://t.me/" + first
    if first.lower() == "joinchat" and len(parts) > 1:
        return "private", None, "https://t.me/+" + parts[1]
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,}", first):
        return "public", "@" + first, f"https://t.me/{first}"
    return None


async def admin_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    state = ctx.user_data.get("state")
    if not state:
        return
    m = update.message
    text = (m.text or "").strip()
    done = InlineKeyboardMarkup([[Btn("🛠 Back to panel", callback_data="adm:home")]])

    if state == "banner":
        if m.video:
            kind, fid = "video", m.video.file_id
        elif m.animation:
            kind, fid = "animation", m.animation.file_id
        elif m.photo:
            kind, fid = "photo", m.photo[-1].file_id
        else:
            await m.reply_text("Send a video, GIF or photo.")
            return
        set_setting("banner_kind", kind)
        set_setting("banner_id", fid)
        ctx.user_data.pop("state")
        await m.reply_text("✅ Banner updated.", reply_markup=done)

    elif state == "key":
        if not text:
            await m.reply_text("Send the key as text.")
            return
        set_setting("key", None if text == "-" else text)
        ctx.user_data.pop("state")
        await m.reply_text("✅ Key cleared." if text == "-" else "✅ Key saved. Users will now receive it.", reply_markup=done)

    elif state == "folder":
        if text == "-":
            set_setting("folder_url", None)
            msg = "✅ Folder removed."
        elif text.startswith("http"):
            set_setting("folder_url", text)
            msg = "✅ Folder link saved. Don't forget to add its channels for verification."
        else:
            await m.reply_text("Send a link like https://t.me/addlist/XXXX or - to remove.")
            return
        ctx.user_data.pop("state")
        await m.reply_text(msg, reply_markup=done)

    elif state == "add":
        ok, dup, bad, queue = 0, 0, [], []
        for line in text.splitlines():
            if not line.strip():
                continue
            r = parse_link(line)
            if not r or r[0] == "folder":
                bad.append(line)
            elif r[0] == "public":
                if add_channel(r[1], r[2]):
                    ok += 1
                else:
                    dup += 1
            else:
                queue.append(r[2])
        msg = f"✅ Added {ok} channel(s)."
        if dup:
            msg += f"\nℹ️ {dup} already in the list."
        if bad:
            msg += "\n⚠️ Not a channel link (folders go in Set folder link):\n" + "\n".join(bad[:10])
        if queue:
            ctx.user_data["priv_queue"] = queue
            ctx.user_data["state"] = "forward"
            await m.reply_text(
                msg + f"\n\n🔒 Private channel: forward ANY post from it to me now.\n{queue[0]}\n"
                "(send <code>skip</code> to skip it)",
                parse_mode=ParseMode.HTML,
                reply_markup=done,
            )
            return
        if ok or dup:
            ctx.user_data.pop("state")
        await m.reply_text(msg, reply_markup=done)

    elif state == "forward":
        queue = ctx.user_data.get("priv_queue") or []
        if not queue:
            ctx.user_data.pop("state", None)
            return
        origin = getattr(m, "forward_origin", None)
        src = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)
        if text.lower() == "skip":
            note = "⏭ Skipped."
        elif src is not None:
            note = "✅ Added." if add_channel(src.id, queue[0]) else "ℹ️ Already in the list."
        else:
            await m.reply_text("Forward a post from that channel (or send skip).")
            return
        queue.pop(0)
        if queue:
            await m.reply_text(f"{note}\n\nNext private channel, forward a post from it:\n{queue[0]}")
            return
        ctx.user_data.pop("state", None)
        ctx.user_data.pop("priv_queue", None)
        await m.reply_text(note, reply_markup=done)

    elif state.startswith("edit:"):
        cid = int(state.split(":")[1])
        r = parse_link(text)
        if not r or r[0] == "folder":
            await m.reply_text("Send a channel link like https://t.me/mychannel")
            return
        update_channel(cid, r[2], r[1] if r[0] == "public" else None)
        ctx.user_data.pop("state")
        await m.reply_text("✅ Channel updated.", reply_markup=done)


def start_health_server():
    """If the host provides a PORT (Render, Koyeb, Replit...), answer pings so it stays 'healthy'."""
    port = os.environ.get("PORT")
    if not port:
        return
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Ping(BaseHTTPRequestHandler):
        def _ok(self):
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            self._ok()
            self.wfile.write(b"ok")

        do_HEAD = _ok

        def log_message(self, *a):
            pass

    threading.Thread(target=HTTPServer(("0.0.0.0", int(port)), Ping).serve_forever, daemon=True).start()


def main():
    start_health_server()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(verify, pattern="^verify$"))
    app.add_handler(CallbackQueryHandler(generate, pattern="^gen$"))
    app.add_handler(CallbackQueryHandler(menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(admin_cb, pattern="^adm:"))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & ((filters.TEXT & ~filters.COMMAND) | filters.VIDEO | filters.ANIMATION | filters.PHOTO | filters.FORWARDED),
            admin_input,
        )
    )
    app.run_polling()


if __name__ == "__main__":
    main()
