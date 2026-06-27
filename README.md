# todo

A fast terminal CLI/TUI for an [Obsidian Kanban](https://github.com/mgmeyers/obsidian-kanban)
board. Manage your tasks from the terminal — vim-style, full-screen — while the
Obsidian file stays the single source of truth. A JSONL mirror lets scripts and
agents read the board cheaply. Pure Python stdlib, zero dependencies.

## Install

```bash
git clone https://github.com/<you>/todo ~/todo
cd ~/todo
./install.sh /path/to/your/To-Do.md         # add --gc-agent for daily auto-expire
```

This puts a `todo` launcher in `~/.local/bin` and writes
`~/.config/todo/config.json`. The board path can also come from `$TODO_BOARD`.
**Your board content and its path are never part of this repo** — they live only
in your local config.

## Use

Run `todo` for the full-screen board:

```
  TODO · To-Do          12 cards

  [1] Inbox  (3)
  › [ ] fix retrieval eval table
    [ ] 線形代数の教科書読む
  [2] This Week  (0)
      —
  [3] Today  (2)
    ...
  j/k move · 1-5 send to lane · a add · x done · e edit
  / search · enter expand · g go-to · r reload · q quit
```

Each lane shows its number, so pressing that number sends the selected card
straight there.

- **j / k** (or arrows) select a card
- **1–9** send the selected card to that lane (numbers shown on each lane)
- **Enter** open the selected card for editing · **Shift+Enter** (or **a**) new card → **Inbox**
- **x** complete (sends the card to **Done**; press again in Done to reopen it)
- **/** search · **g** jump to a lane · **r** reload · **Esc**/**q** quit

Cards open in a multi-line editor for detailed notes. On terminals with the
kitty keyboard protocol (Ghostty, kitty, WezTerm, recent iTerm2): **Enter** =
save, **Shift+Enter** = new line, **Esc** = cancel. On other terminals it falls
back to **Enter** = new line, **Ctrl-D** = save (override with
`TODO_KBD=kitty|legacy`). The first line is the card title; the rest is its body.

Cards made by agents carry a `#tag`; their `[ ]` box is tinted a gentle color,
one consistent color per tag, so you can see at a glance whose card is whose.

Completion is the lane: a card in **Done** is checked `[x]`, a card anywhere else
is `[ ]`, and moving a card in/out of Done flips it automatically. Any `[x]` card
that ends up outside Done — e.g. ticked directly in Obsidian — is auto-filed into
**Done** on the next run (and on TUI reload), so checking a box anywhere always
means "done."

### Scripting / agents

```bash
todo ls [--lane L] [--tag T] [--json]   # list (refreshes the JSONL mirror)
todo search <query>
todo add "buy milk" [--lane Inbox] [--tag "#me"]
todo mv <id> <lane>       # id + lane index/name from `todo ls`
todo done <id>            # complete: move the card to Done (auto-checked)
todo view [query]         # plain-text board dump
todo gc [--days N] [--dry-run]   # expire old Done cards now
todo sync                 # rebuild the JSONL mirror
todo path
```

## How it stays safe

- **Obsidian is canonical.** Edits rewrite `To-Do.md` preserving its exact
  format (frontmatter, styled `## lane` headers, multi-line cards, the
  `%% kanban:settings %%` block). A round-trip of an unchanged board is
  byte-identical.
- Every write is **atomic** (temp file + rename) and **re-reads immediately
  before editing**, so it's safe under iCloud sync and concurrent edits.
- The **JSONL mirror** (`To-Do.jsonl` next to the board) is regenerated from the
  markdown on every change — a read-only machine view, never a second source.

## Auto-expire (gc)

Cards in the **Done** lane are deleted after `retain_done_days` (default 7). The
clock starts when the CLI first sees a card in Done (tracked in
`~/.config/todo/done_seen.json`, so the board format stays clean). Deleted cards
are appended to `~/.config/todo/gc-trash.jsonl` — nothing is lost. `gc` runs
lazily (~once/day) when you use the tool, or daily via the optional
`--gc-agent` launchd job.
