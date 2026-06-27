#!/usr/bin/env python3
"""todo — a terminal CLI/TUI for an Obsidian Kanban board.

The Obsidian board (To-Do.md) stays the source of truth. This tool edits it
while preserving the Kanban markdown exactly, mirrors it to a JSONL file for
cheap machine reading, and auto-expires old Done cards.

Config (never committed) at ~/.config/todo/config.json:
  { "board": "/path/To-Do.md", "mirror": "/path/To-Do.jsonl",
    "retain_done_days": 7, "done_lane": "Done" }
Board path may also come from $TODO_BOARD.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "todo" / "config.json"
SEEN_PATH = Path.home() / ".config" / "todo" / "done_seen.json"
TRASH_PATH = Path.home() / ".config" / "todo" / "gc-trash.jsonl"

CARD_RE = re.compile(r"^- \[[ xX]\] ")
DONE_RE = re.compile(r"^- \[[xX]\] ")
HEADER_RE = re.compile(r"^## ")
HTML_RE = re.compile(r"<[^>]+>")
TAG_RE = re.compile(r"#[A-Za-z0-9_\-/]+")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    cfg = {}
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text())
    if os.environ.get("TODO_BOARD"):
        cfg["board"] = os.environ["TODO_BOARD"]
    if not cfg.get("board"):
        sys.exit("No board configured. Set $TODO_BOARD or run install.sh "
                 "(writes ~/.config/todo/config.json).")
    cfg.setdefault("retain_done_days", 7)
    cfg.setdefault("done_lane", "Done")
    return cfg


def board_path():
    return Path(load_config()["board"])


def mirror_path():
    cfg = load_config()
    if cfg.get("mirror"):
        return Path(cfg["mirror"])
    return board_path().with_suffix(".jsonl")


# ---------------------------------------------------------------------------
# Model + parser (lossless: serialize(parse(x)) == x)
# ---------------------------------------------------------------------------
class Lane:
    def __init__(self, header_raw, preamble, cards):
        self.header_raw = header_raw                       # e.g. '## <mark...>Today</mark>'
        self.preamble = preamble                           # raw lines before first card
        self.cards = cards                                 # list[list[str]] raw card blocks
        self.name = strip_html(re.sub(HEADER_RE, "", header_raw)).strip()


class Board:
    def __init__(self, head, lanes, tail):
        self.head = head                                   # lines before first lane (frontmatter)
        self.lanes = lanes                                 # working lanes (before ***)
        self.tail = tail                                   # *** + archive store + settings block


def strip_html(s):
    return HTML_RE.sub("", s)


def parse(text):
    lines = text.split("\n")
    n = len(lines)
    first_lane = next((i for i, l in enumerate(lines) if HEADER_RE.match(l)), None)
    if first_lane is None:
        return Board(lines, [], [])
    settings_idx = next((i for i, l in enumerate(lines)
                         if l.startswith("%% kanban:settings")), None)
    limit = settings_idx if settings_idx is not None else n
    star_idx = next((i for i in range(first_lane, limit) if lines[i].strip() == "***"), None)
    tail_start = star_idx if star_idx is not None else (settings_idx if settings_idx is not None else n)

    head = lines[:first_lane]
    working = lines[first_lane:tail_start]
    tail = lines[tail_start:]

    lanes, i = [], 0
    while i < len(working):
        header = working[i]; i += 1
        body_start = i
        while i < len(working) and not HEADER_RE.match(working[i]):
            i += 1
        lanes.append(_make_lane(header, working[body_start:i]))
    return Board(head, lanes, tail)


def _make_lane(header, body):
    idx = 0
    while idx < len(body) and not CARD_RE.match(body[idx]):
        idx += 1
    preamble = body[:idx]
    cards = []
    while idx < len(body):
        start = idx; idx += 1
        while idx < len(body) and not CARD_RE.match(body[idx]):
            idx += 1
        cards.append(body[start:idx])
    return Lane(header, preamble, cards)


def serialize(board):
    out = list(board.head)
    for lane in board.lanes:
        out.append(lane.header_raw)
        out.extend(lane.preamble)
        for c in lane.cards:
            out.extend(c)
    out.extend(board.tail)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Card helpers
# ---------------------------------------------------------------------------
def card_title(block):
    line = block[0]
    line = CARD_RE.sub("", line, count=1)
    return strip_html(line).strip()


def card_done(block):
    return bool(DONE_RE.match(block[0]))


def card_tags(block):
    # tags live in the visible text, not inside HTML attributes (e.g. color hexes)
    return TAG_RE.findall(strip_html(block[0]))


def card_id(title):
    return hashlib.sha1(title.encode("utf-8")).hexdigest()[:6]


def card_body(block):
    return "\n".join(l.strip() for l in block[1:]).strip()


# ---------------------------------------------------------------------------
# I/O — atomic write + JSONL mirror
# ---------------------------------------------------------------------------
def read_board():
    return parse(board_path().read_text(encoding="utf-8"))


def write_board(text):
    p = board_path()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".todo-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)
    write_mirror(parse(text))


def to_records(board):
    recs = []
    for li, lane in enumerate(board.lanes):
        for block in lane.cards:
            title = card_title(block)
            recs.append({
                "id": card_id(title), "lane": lane.name, "lane_index": li + 1,
                "done": card_done(block), "tags": card_tags(block),
                "title": title, "body": card_body(block),
            })
    return recs


def write_mirror(board):
    p = mirror_path()
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in to_records(board))
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".todo-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text + ("\n" if text else ""))
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
def find_lane(board, ref):
    """Resolve a lane by 1-based index or name substring."""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        idx = int(ref) - 1
        if 0 <= idx < len(board.lanes):
            return idx
        return None
    rl = str(ref).lower()
    for i, lane in enumerate(board.lanes):
        if rl in lane.name.lower():
            return i
    return None


def find_card(board, cid):
    for li, lane in enumerate(board.lanes):
        for ci, block in enumerate(lane.cards):
            if card_id(card_title(block)) == cid:
                return li, ci
    return None


# ---------------------------------------------------------------------------
# Mutations — operate on a fresh read, then write
# ---------------------------------------------------------------------------
def op_move(lane_name, title, dest_idx):
    board = read_board()
    src = next((i for i, l in enumerate(board.lanes) if l.name == lane_name), None)
    if src is None or not (0 <= dest_idx < len(board.lanes)):
        return False
    ci = next((i for i, b in enumerate(board.lanes[src].cards) if card_title(b) == title), None)
    if ci is None:
        return False
    block = board.lanes[src].cards.pop(ci)
    while block and block[-1].strip() == "":          # drop trailing blank lines on move
        block.pop()
    # the checkbox mirrors the lane: into Done -> [x], out of Done -> [ ]
    done_name = load_config()["done_lane"].lower()
    if board.lanes[dest_idx].name.lower() == done_name:
        if block[0].startswith("- [ ] "):
            block[0] = "- [x] " + block[0][6:]
    elif DONE_RE.match(block[0]):
        block[0] = "- [ ] " + block[0][6:]
    board.lanes[dest_idx].cards.append(block)
    write_board(serialize(board))
    return True


def _block_from_text(prefix, text):
    """Build a card block from possibly-multi-line text: first line is the card,
    the rest are tab-indented continuation lines (Obsidian Kanban card body)."""
    lines = text.split("\n")
    return [prefix + lines[0]] + ["\t" + l for l in lines[1:]]


def card_raw_text(block):
    """Inverse of _block_from_text: the editable text of a card (checkbox stripped,
    continuation lines de-indented), preserving the raw markdown for editing."""
    first = CARD_RE.sub("", block[0], count=1)
    rest = [l[1:] if l.startswith("\t") else l for l in block[1:]]
    return "\n".join([first] + rest)


def op_set_card(lane_name, title, new_text):
    """Replace a card's full content (title + body), keeping its [ ]/[x] state."""
    board = read_board()
    for lane in board.lanes:
        if lane.name != lane_name:
            continue
        for i, block in enumerate(lane.cards):
            if card_title(block) == title:
                prefix = block[0][:6] if CARD_RE.match(block[0]) else "- [ ] "
                lane.cards[i] = _block_from_text(prefix, new_text)
                write_board(serialize(board))
                return True
    return False


def op_add(dest_idx, text, tag=None):
    board = read_board()
    if not (0 <= dest_idx < len(board.lanes)):
        return False
    if tag and not text.startswith(tag):
        text = tag + " " + text
    board.lanes[dest_idx].cards.append(_block_from_text("- [ ] ", text))
    write_board(serialize(board))
    return True


# ---------------------------------------------------------------------------
# Garbage collection — expire old Done cards
# ---------------------------------------------------------------------------
def _now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def _load_seen():
    if SEEN_PATH.exists():
        return json.loads(SEEN_PATH.read_text())
    return {}


def _save_seen(seen):
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=2))


def gc(days=None, dry_run=False, force=False):
    """Delete Done cards first seen more than `days` ago. Returns deleted titles."""
    cfg = load_config()
    days = cfg["retain_done_days"] if days is None else days
    seen = _load_seen()

    if not force and not dry_run:
        last = seen.get("_last_gc")
        if last:
            try:
                if (dt.datetime.now() - dt.datetime.fromisoformat(last)).total_seconds() < 12 * 3600:
                    return []                              # ran recently; skip the lazy pass
            except ValueError:
                pass

    board = read_board()
    done_li = find_lane(board, cfg["done_lane"])
    now = dt.datetime.now()
    deleted = []
    if done_li is not None:
        lane = board.lanes[done_li]
        present = set()
        keep = []
        for block in lane.cards:
            title = card_title(block)
            cid = card_id(title)
            present.add(cid)
            first = seen.get(cid)
            if first is None:
                seen[cid] = _now_iso()                     # start the clock now
                keep.append(block)
                continue
            try:
                age = (now - dt.datetime.fromisoformat(first)).total_seconds()
            except ValueError:
                age = 0
            if age > days * 86400:
                deleted.append({"title": title, "body": card_body(block),
                                "done_since": first, "removed": _now_iso()})
            else:
                keep.append(block)
        if deleted and not dry_run:
            lane.cards = keep
            write_board(serialize(board))
            with open(TRASH_PATH, "a", encoding="utf-8") as f:
                for d in deleted:
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")
        # prune ids no longer in Done
        for cid in [k for k in seen if k != "_last_gc" and k not in present]:
            del seen[cid]
    if not dry_run:
        seen["_last_gc"] = _now_iso()
        _save_seen(seen)
    return [d["title"] for d in deleted]


def maybe_gc():
    try:
        gc()
    except Exception:
        pass                                                # never let gc break a command


def reconcile():
    """Ticking = done: move any [x] card that isn't in the Done lane into Done.

    Keeps the two ways of checking a card consistent — the CLI's `x`/`done` and
    a checkbox ticked directly in Obsidian both end up filed in Done.
    """
    cfg = load_config()
    board = read_board()
    done_idx = find_lane(board, cfg["done_lane"])
    if done_idx is None:
        return 0
    strays = [(lane.name, card_title(b))
              for li, lane in enumerate(board.lanes) if li != done_idx
              for b in lane.cards if card_done(b)]
    for lane_name, title in strays:
        op_move(lane_name, title, done_idx)                 # re-reads + writes each move
    return len(strays)


def maybe_reconcile():
    try:
        reconcile()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rendering — pure function (text rows tagged by kind), used by TUI and `view`
# ---------------------------------------------------------------------------
def matches(query, block):
    if not query:
        return True
    q = query.lower()
    return any(q in l.lower() for l in block)


def build_view(board, sel, query=""):
    rows = []
    total = sum(len(l.cards) for l in board.lanes)
    rows.append((f"  TODO · {board_path().stem}    {total} cards"
                 + (f'   /{query}' if query else ""), "title"))
    rows.append(("", "sep"))
    flat = []
    for li, lane in enumerate(board.lanes):
        vis = [(ci, b) for ci, b in enumerate(lane.cards) if matches(query, b)]
        if query and not vis:
            continue
        rows.append((f"  [{li+1}] {lane.name}  ({len(lane.cards)})", "lane"))
        for ci, b in vis:
            idx = len(flat)
            flat.append((li, ci))
            mark = "›" if idx == sel else " "
            done = "✓" if card_done(b) else " "
            rows.append((f"  {mark} [{done}] {card_title(b)}",
                         "card_sel" if idx == sel else "card"))
        if not vis:
            rows.append(("      —", "empty"))
    rows.append(("", "sep"))
    rows.append(("  j/k move · 1-{n} send to lane · a add · x done · e edit"
                 .format(n=len(board.lanes)), "foot"))
    rows.append(("  / search · enter expand · g go-to · r reload · q quit", "foot"))
    return rows, flat


# ---------------------------------------------------------------------------
# TUI (curses)
# ---------------------------------------------------------------------------
def run_tui():
    import curses
    curses.wrapper(_tui)


def _tui(stdscr):
    import curses
    curses.curs_set(0)
    stdscr.keypad(True)
    sel, query, msg = 0, "", ""
    board = read_board()
    done_name = load_config()["done_lane"].lower()

    def prompt(label, prefill=""):
        curses.curs_set(1)
        buf = list(prefill)
        h, w = stdscr.getmaxyx()
        while True:
            stdscr.move(h - 1, 0); stdscr.clrtoeol()
            stdscr.addnstr(h - 1, 0, (label + "".join(buf))[:w - 1], w - 1)
            c = stdscr.getch()
            if c in (10, 13):
                curses.curs_set(0); return "".join(buf)
            if c == 27:
                curses.curs_set(0); return None
            if c in (curses.KEY_BACKSPACE, 127, 8):
                if buf: buf.pop()
            elif 32 <= c < 0x110000:
                try: buf.append(chr(c))
                except ValueError: pass

    def medit(label, prefill=""):
        """Full-screen multi-line editor. Enter=new line, Ctrl-D=save, Esc=cancel."""
        curses.curs_set(1)
        lines = prefill.split("\n") if prefill else [""]
        cy, cx = len(lines) - 1, len(lines[-1])
        try:
            while True:
                h, w = stdscr.getmaxyx()
                maxvis = max(3, h - 4)
                top = max(0, cy - (maxvis - 1))
                stdscr.erase()
                stdscr.addnstr(0, 0, label[:w - 1], w - 1, curses.A_BOLD)
                stdscr.addnstr(1, 0, ("─" * (w - 1))[:w - 1], w - 1, curses.A_DIM)
                for i, ln in enumerate(lines[top:top + maxvis]):
                    try: stdscr.addnstr(2 + i, 0, ln[:w - 1], w - 1)
                    except curses.error: pass
                stdscr.addnstr(h - 1, 0,
                               "Enter: new line   Ctrl-D: save   Esc: cancel"[:w - 1],
                               w - 1, curses.A_DIM)
                try: stdscr.move(2 + (cy - top), min(cx, w - 1))
                except curses.error: pass
                stdscr.refresh()
                ch = stdscr.get_wch()
                if isinstance(ch, str):
                    if ch == "\x04":                 # Ctrl-D = save
                        curses.curs_set(0); return "\n".join(lines)
                    if ch == "\x1b":                 # Esc = cancel
                        curses.curs_set(0); return None
                    if ch in ("\n", "\r"):
                        rest = lines[cy][cx:]; lines[cy] = lines[cy][:cx]
                        lines.insert(cy + 1, rest); cy += 1; cx = 0
                    elif ch in ("\x7f", "\b"):
                        if cx > 0: lines[cy] = lines[cy][:cx - 1] + lines[cy][cx:]; cx -= 1
                        elif cy > 0:
                            cx = len(lines[cy - 1]); lines[cy - 1] += lines[cy]
                            del lines[cy]; cy -= 1
                    else:
                        lines[cy] = lines[cy][:cx] + ch + lines[cy][cx:]; cx += len(ch)
                else:
                    if ch == curses.KEY_BACKSPACE:
                        if cx > 0: lines[cy] = lines[cy][:cx - 1] + lines[cy][cx:]; cx -= 1
                        elif cy > 0:
                            cx = len(lines[cy - 1]); lines[cy - 1] += lines[cy]
                            del lines[cy]; cy -= 1
                    elif ch == curses.KEY_LEFT: cx = max(0, cx - 1)
                    elif ch == curses.KEY_RIGHT: cx = min(len(lines[cy]), cx + 1)
                    elif ch == curses.KEY_UP and cy > 0: cy -= 1; cx = min(cx, len(lines[cy]))
                    elif ch == curses.KEY_DOWN and cy < len(lines) - 1: cy += 1; cx = min(cx, len(lines[cy]))
                    elif ch == curses.KEY_DC and cx < len(lines[cy]):
                        lines[cy] = lines[cy][:cx] + lines[cy][cx + 1:]
        except Exception:
            curses.curs_set(0); return None

    while True:
        rows, flat = build_view(board, sel, query)
        if flat: sel = max(0, min(sel, len(flat) - 1))
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        for y, (text, kind) in enumerate(rows):
            if y >= h - 1: break
            attr = curses.A_NORMAL
            if kind in ("title", "lane"): attr = curses.A_BOLD
            elif kind == "bar": attr = curses.A_DIM | curses.A_BOLD
            elif kind == "card_sel": attr = curses.A_REVERSE
            elif kind in ("foot", "empty", "sep"): attr = curses.A_DIM
            try: stdscr.addnstr(y, 0, text[:w - 1], w - 1, attr)
            except curses.error: pass
        if msg:
            try: stdscr.addnstr(h - 1, 0, ("  " + msg)[:w - 1], w - 1, curses.A_DIM)
            except curses.error: pass
        stdscr.refresh()

        c = stdscr.getch()
        msg = ""
        sel_card = flat[sel] if flat and sel < len(flat) else None

        def cur_title_lane():
            li, ci = sel_card
            return board.lanes[li].name, card_title(board.lanes[li].cards[ci])

        if c in (ord("q"), 27 if not query else -999):
            break
        elif c in (ord("j"), curses.KEY_DOWN):
            sel += 1
        elif c in (ord("k"), curses.KEY_UP):
            sel = max(0, sel - 1)
        elif ord("1") <= c <= ord("9"):
            dest = c - ord("1")
            if sel_card and dest < len(board.lanes):
                ln, ti = cur_title_lane()
                op_move(ln, ti, dest); board = read_board()
                msg = f"moved to {board.lanes[dest].name}"
        elif c == ord("x") and sel_card:
            li = sel_card[0]
            ln, ti = cur_title_lane()
            done_idx = next((i for i, l in enumerate(board.lanes)
                             if l.name.lower() == done_name), len(board.lanes) - 1)
            if li == done_idx:                       # already done -> reopen into the first lane
                op_move(ln, ti, 0); board = read_board(); msg = "reopened → " + board.lanes[0].name
            else:
                op_move(ln, ti, done_idx); board = read_board(); msg = "done → " + board.lanes[done_idx].name
        elif c == ord("a"):
            inbox = next((i for i, l in enumerate(board.lanes)
                          if l.name.lower() == "inbox"), 0)
            t = medit(f"New card → {board.lanes[inbox].name}")
            if t and t.strip():
                op_add(inbox, t.strip("\n")); board = read_board(); msg = "added → " + board.lanes[inbox].name
        elif c == ord("e") and sel_card:
            li, ci = sel_card
            ln, ti = cur_title_lane()
            nt = medit("Edit card", card_raw_text(board.lanes[li].cards[ci]))
            if nt is not None and nt.strip():
                op_set_card(ln, ti, nt.strip("\n")); board = read_board(); msg = "edited"
        elif c == ord("/"):
            q = prompt("/", query); query = q or ""; sel = 0
        elif c == ord("g"):
            g = prompt("go to lane #: ")
            if g and g.isdigit():
                target = int(g) - 1
                pos = next((i for i, (li, _) in enumerate(flat) if li == target), None)
                if pos is not None: sel = pos
        elif c in (10, 13) and sel_card:
            li, ci = sel_card
            _expand(stdscr, board.lanes[li].cards[ci])
        elif c == ord("r"):
            maybe_reconcile(); board = read_board(); msg = "reloaded"


def _expand(stdscr, block):
    import curses
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    lines = ["  " + card_title(block), ""] + ["  " + l for l in block[1:]] + ["", "  (any key)"]
    for y, l in enumerate(lines):
        if y >= h - 1: break
        try: stdscr.addnstr(y, 0, l[:w - 1], w - 1)
        except curses.error: pass
    stdscr.refresh(); stdscr.getch()


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
def cmd_ls(a):
    board = read_board(); write_mirror(board)
    recs = to_records(board)
    if a.lane:
        li = find_lane(board, a.lane)
        recs = [r for r in recs if li is not None and r["lane_index"] == li + 1]
    if a.tag:
        recs = [r for r in recs if a.tag in r["tags"] or ("#" + a.tag) in r["tags"]]
    if a.json:
        for r in recs: print(json.dumps(r, ensure_ascii=False))
        return
    cur = None
    for r in recs:
        if r["lane"] != cur:
            cur = r["lane"]; print(f"\n## {cur}")
        print(f"  {r['id']}  [{'x' if r['done'] else ' '}] {r['title']}")


def cmd_search(a):
    board = read_board()
    q = a.query.lower()
    for li, lane in enumerate(board.lanes):
        for b in lane.cards:
            if any(q in l.lower() for l in b):
                print(f"  {card_id(card_title(b))}  [{lane.name}] {card_title(b)}")


def cmd_add(a):
    board = read_board()
    li = find_lane(board, a.lane) if a.lane else find_lane(board, "Inbox")
    if li is None: li = 0
    op_add(li, a.text, a.tag); print(f"added to {board.lanes[li].name}")


def cmd_mv(a):
    board = read_board()
    loc = find_card(board, a.id)
    if loc is None: sys.exit(f"no card with id {a.id}")
    dest = find_lane(board, a.lane)
    if dest is None: sys.exit(f"no lane {a.lane}")
    ln = board.lanes[loc[0]].name; ti = card_title(board.lanes[loc[0]].cards[loc[1]])
    op_move(ln, ti, dest); print(f"moved {a.id} -> {board.lanes[dest].name}")


def cmd_done(a):
    board = read_board()
    loc = find_card(board, a.id)
    if loc is None: sys.exit(f"no card with id {a.id}")
    done_idx = find_lane(board, load_config()["done_lane"])
    if done_idx is None: done_idx = len(board.lanes) - 1
    ti = card_title(board.lanes[loc[0]].cards[loc[1]])
    op_move(board.lanes[loc[0]].name, ti, done_idx)
    print(f"done -> {board.lanes[done_idx].name}: {ti}")


def cmd_view(a):
    rows, _ = build_view(read_board(), -1, a.query or "")
    for text, _ in rows: print(text)


def cmd_sync(a):
    write_mirror(read_board()); print(f"mirror -> {mirror_path()}")


def cmd_gc(a):
    titles = gc(days=a.days, dry_run=a.dry_run, force=True)
    verb = "would delete" if a.dry_run else "deleted"
    if titles:
        print(f"{verb} {len(titles)} done card(s):")
        for t in titles: print("  -", t)
    else:
        print("nothing to expire")


def cmd_path(a):
    print("board: ", board_path())
    print("mirror:", mirror_path())


def main():
    p = argparse.ArgumentParser(prog="todo", description="Terminal CLI/TUI for the Obsidian Kanban board.")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("ls"); s.add_argument("--lane"); s.add_argument("--tag")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_ls)
    s = sub.add_parser("search"); s.add_argument("query"); s.set_defaults(fn=cmd_search)
    s = sub.add_parser("add"); s.add_argument("text"); s.add_argument("--lane"); s.add_argument("--tag")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("mv"); s.add_argument("id"); s.add_argument("lane"); s.set_defaults(fn=cmd_mv)
    s = sub.add_parser("done"); s.add_argument("id"); s.set_defaults(fn=cmd_done)
    s = sub.add_parser("view"); s.add_argument("query", nargs="?"); s.set_defaults(fn=cmd_view)
    s = sub.add_parser("sync"); s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("gc"); s.add_argument("--days", type=int); s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_gc)
    s = sub.add_parser("path"); s.set_defaults(fn=cmd_path)
    sub.add_parser("help")

    args = p.parse_args()
    if args.cmd == "help":
        p.print_help(); return
    maybe_reconcile()               # file any [x] card (incl. ticked in Obsidian) into Done
    if not args.cmd:
        maybe_gc()
        run_tui()
        return
    if args.cmd != "gc":
        maybe_gc()
    args.fn(args)


if __name__ == "__main__":
    main()
