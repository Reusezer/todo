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
  [1] Inbox   [2] This Week   [3] Today   [4] Doing   [5] Done

  Inbox (3)
  › [ ] fix retrieval eval table
    [ ] 線形代数の教科書読む
  ...
  j/k move · 1-5 send to lane · a add · x done · e edit
  / search · enter expand · g go-to · r reload · q quit
```

- **j / k** (or arrows) select a card
- **1–9** send the selected card to that lane (numbers shown in the bar)
- **a** add · **x** toggle done · **e** edit · **enter** expand · **/** search ·
  **g** jump to a lane · **r** reload · **q** quit

### Scripting / agents

```bash
todo ls [--lane L] [--tag T] [--json]   # list (refreshes the JSONL mirror)
todo search <query>
todo add "buy milk" [--lane Inbox] [--tag "#me"]
todo mv <id> <lane>       # id + lane index/name from `todo ls`
todo done <id>            # toggle complete
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
