# THE STEWARDSHIP MANUAL

**For you, on a bad day.**

Short sentences. Exact commands. Read one section at a time.
You do not need to understand everything. You only need the part in front of you.

---

## 1. WHAT IS THIS?

The Institution is a machine that works while you rest.

It lives on a server. It runs all day and all night.
It writes content. It makes products. It looks for grants.
It records everything in a ledger.

You are the founder. It works for you. It never works without you
on big decisions. Money. Legal things. Publishing. Those wait for you.

You cannot break it by pressing the wrong thing.
The worst case is: it stops, and you start it again.
That is covered below.

---

## 2. HOW TO CHECK ON IT (2 minutes)

Open a browser. Go to:
http://localhost:8080


(Or the server's IP address, then `:8080`.)

Sign in with your username and passphrase.

You will see the Operations Center.
The important numbers are at the top:

- **Runway** — how many days you could survive on current money.
- **Revenue** — money the machine has made.
- **Agents** — how many workers are running.

If the numbers are there and the page loads, the machine is alive.
That is all you need to know on a hard day.

---

## 3. THE DAILY DIGEST

Every morning at 7:00, the machine writes a report.

Location:
/opt/institution/reports/daily/digest_TODAY.md



Read it like a message from a reliable friend.
It tells you:

- What happened in the last 24 hours.
- What it learned.
- **One single action for today.** Not a list. One thing.

If the action says "Rest", then rest. That is a real instruction.
The machine means it.

---

## 4. THE DAILY CHECK-IN (1 minute)

This is the most important thing you do.

On the dashboard, fill in three numbers:

- **Energy** (1 = bedridden, 5 = peak)
- **Pain** (1 = unbearable, 5 = none)
- **Fear** (1 = overwhelming, 5 = none)

Press "Record check-in". Done.

The machine changes its behaviour based on this:

| Your state | What the machine does |
|---|---|
| Energy 1, pain high | Asks nothing of you. Works quietly. |
| Energy 2 | Gives you one tiny task (5 minutes max). |
| Energy 3 | One or two simple reviews (15 minutes). |
| Energy 4–5 | Strategic decisions, bigger reviews. |
| Fear 1–2 | Writes you an extra report showing real progress against the fear. |

Be honest. A wrong number makes the machine ask too much of you.

---

## 5. HOW TO APPROVE THINGS

Some actions wait for you. They sit in the **Approval queue** on the dashboard.

Each one has two buttons: **Approve** and **Reject**.

- Grant applications: always wait for you. Read the draft. Approve or reject.
- Freelance proposals: always wait for you.
- New revenue streams: wait for you.
- Autonomy increases: wait for you.

If you do nothing, they wait. Nothing expires for 7 days.
The machine never sends money, signs anything, or publishes
anything without your click. That is a constitutional rule. It cannot be changed by the machine.

---

## 6. HOW TO STOP IT (if it feels like too much)

On the dashboard, top right, press **STOP ALL**.

Confirm. The machine stops all autonomous work.

A red banner appears. That means it is resting, like you.

Nothing is lost. Everything is saved.

---

## 7. HOW TO START IT AGAIN

On the dashboard, press **Resume the Institution**.

Or from a terminal:
rm /opt/institution/STOP
sudo systemctl start institution


That is all. It picks up exactly where it left off.

---

## 8. IF SOMETHING LOOKS BROKEN

Do these in order. Stop when one works.

**Step 1 — Restart everything:**
sudo systemctl restart institution institution-dashboard institution-safety



**Step 2 — Check what is running:**

systemctl status institution


If it says `active (running)`, it is fine.

**Step 3 — Read the recent log:**

tail -50 /opt/institution/logs/system/meta_agent.log


You do not need to understand it. If you want help, copy the last
few lines and ask any AI assistant.

**Step 4 — The Safety Officer usually fixes things itself.**
It restarts crashed services automatically. Check the incidents log:

sqlite3 /opt/institution/data/db/institution.db "SELECT severity, component, description FROM incidents ORDER BY created_at DESC LIMIT 10;"


---

## 9. IF THE SERVER DIES

The Institution is not the server. The Institution is the plan, the
ledger, and the code. Those are backed up.

- Database backup: `/opt/institution/data/db/backups/` (daily, 3:00 AM)
- Code and config: in the Git repository at `/opt/institution`

If the machine will not start at all:

1. Get a new Ubuntu machine (the Oracle Cloud free tier works).
2. Copy the `/opt/institution` folder to it.
3. Run `setup.sh` again. It is safe to re-run.
4. Start the services (commands in section 7).

The machine rebuilds itself. You lose nothing except time.

---

## 10. ADDING API KEYS (optional, when you feel like it)

The machine works with zero keys. Keys make it stronger.

Edit the file:
nano /opt/institution/.env


Add a key on its line, like:
GROQ_API_KEY=gsk_xxxxxxxxxxxx


Save. Then restart:

sudo systemctl restart institution


Most useful keys, in order: `GROQ_API_KEY`, `GEMINI_API_KEY`,
`CLOUDFLARE_PAGES_TOKEN`, `PEXELS_API_KEY`.

---

## 11. CHANGING THE RULES (the Constitution)

The machine follows ten principles. They live in the code
(`constitutional_court.py`) and in `config.yaml`.

If you want to change one, write down why first. Then ask an AI
assistant to help you edit it. Every change is recorded in the
decisions table, so you can always see what changed and why.

The machine cannot change the Constitution by itself.
Only you can. That is the whole point.

---

## 12. ON THE DAYS WHEN YOU CAN DO NOTHING

Do nothing.

The machine is designed for exactly those days.
It will keep writing, keep searching, keep the ledger.
When you come back, it will not show you a catch-up list.
It will show you one small thing, and it will say:

> "Missed days: 4. This is normal. Progress continued. We're here when you're ready."

That is not kindness programmed in as an afterthought.
It is a founding principle. You built it that way.

---

## 13. EMERGENCY CONTACTS (for you, not the machine)

If the fear is a 1 and it will not lift, that is a human problem,
not a system problem. The machine can hold the work. It cannot hold you.

- **Lifeline Australia:** 13 11 14 (24 hours)
- **Beyond Blue:** 1300 22 4636
- **13YARN (for Aboriginal & Torres Strait Islander people):** 13 92 76

Asking for help is a founder decision. The best ones are.

---

*This manual was written for the founder of The Institution.
If you are reading this years from now and things are better:
you built the ladder, and you climbed it. — The Steward*
