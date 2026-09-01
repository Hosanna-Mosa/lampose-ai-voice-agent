# LAMPOSE Voice AI — index

Telugu AI calling agent that phones property owners (PG / hostel / To-Let /
hotel) and onboards them onto LAMPOSE. Live at **voice.lampose.com** on the
user's own VPS. Python 3.12 in `venv/`, deployed by `git pull` on the VPS.

This file is deliberately short: it is loaded into every session, and its job is
only to point at the right document. Keep area notes in `docs/`, one file per
area, and add a line here when you create one.

| Document | What it covers |
|---|---|
| `docs/PROJECT_CONTEXT.md` | The whole project: architecture, decisions, gotchas, latency, open items, roadmap. **Read this first.** |
| `docs/VOICE_PIPELINE.md` | The call pipeline — commands, house rules, the landmines that each cost a broken call, and how to resume the session it was built in. |
| `docs/telugu_script.md` | The Telugu call script. |

## Before pushing anything

```bash
for t in tests/test_*.py; do PYTHONPATH=. ./venv/bin/python $t; done
```

All six suites must pass. The user deploys by hand on the VPS — give them one
command at a time, never a batch.
