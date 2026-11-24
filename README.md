# Prompted OS

**You decide. Your agents do the rest.**

The free, open-source edition of the Prompted Operating System: an AI chief of staff, **Alfred**, and a team of AI agents
that work on your projects every day and wait for your yes. It runs on your own computer, with your own Claude account.
Your notes and your data stay in your own folders.

## Download

Get the installer for your computer from **[Releases](../../releases/latest)**:

- **macOS** (Apple silicon, macOS 15 or later): `Prompted-OS-Mac.dmg`. The native app, with the clap ritual and
  push-to-talk dictation. It is not notarized yet: the first time, open it from Applications, then allow it in
  System Settings → Privacy & Security → **Open Anyway**.
- **Windows** (x64): `Prompted-OS-Windows.exe`. Windows SmartScreen may warn about an unknown publisher:
  **More info → Run anyway**. The clap ritual and the WhatsApp and inbox services are macOS only for now.

On first launch it asks your name, opens the Claude sign-in in your browser and, if you want, your notes folder and the
code projects for the agent team. Anything you do not set up stays off.

You need a **Claude account (Pro or Max)**. Optional: **Python 3** for the agent team, an **ElevenLabs** key for the
natural voice (otherwise the system voice), a **Telegram** bot of your own, **whisper.cpp** for voice notes, and
**Codex** or **Hermes** if you already use them.

## What is inside

- **Alfred**: talk or type; it answers with voice and proposes one clear action at a time. The same assistant on
  Telegram and in a WhatsApp group.
- **The clap ritual** (Mac): clap twice and Alfred greets you with your day, plays the music you chose and listens.
- **A team of AI agents**: six departments (Operations, Intelligence, Product, Creative, Business, Control) plan every
  morning, build in their own branch, review each other and wait for your approval. One task per agent, a receipt for
  every change. Claude builds and reviews; with **Codex** installed, one builds and the other reviews.
- **Hermes** (optional): give it a goal and it carries it to the end with the team, proposing first how to measure it.
- **Your day**: a short to-do every morning, a day and week close, spaced-repetition review, Calendar and Reminders.
- **Projects and Deliveries**: your portfolio, read from your notes, and everything that waits for your decision,
  explained in plain words with one button per decision.
- **Memory and inbox**: your own notes folder (Obsidian works) as long-term memory; ideas, links, PDFs and voice notes
  you send to the WhatsApp group land in your inbox and in the right project.

## Build it yourself

```sh
cd alfred-os && npm ci && cd ../mac/local-workspace && npm ci
cd ../ && ./build.sh && ./test.sh                     # native macOS app
cd ../escritorio && npm install && node preparar.mjs && npx electron-builder --win   # Windows
```

Tests: `python -m unittest discover -s equipo`, `python -m unittest discover -s hermes`, `mac/test.sh` (Swift, local
server, inbox and WhatsApp against fakes). The folders: `alfred-os` (interface), `mac` (native macOS app and its local
server), `escritorio` (desktop app for Windows), `equipo` (agent team engine), `hermes` (goals end to end), `whatsapp`
(Alfred in a WhatsApp group) and `inbox` (ideas inbox).

WhatsApp works as a linked device of a number of its own: use a separate number for Alfred, never your personal one.
It is not an official WhatsApp client and WhatsApp may block it.

## Principles

- **You hold the keys.** Nothing is published, sent, spent or traded without your explicit yes.
- **Local first.** It runs on your machine. No servers of ours in between.
- **No invented progress.** Dates, evidence and limits. If something isn't verified, it says so.

## Want it set up for your company?

PROMPTED installs the full Prompted Operating System in businesses: your departments, your rules and your tools, with a
30-day pilot on one process. [workwithprompted.com](https://www.workwithprompted.com/#/os)

## License

[GNU Affero General Public License v3.0](LICENSE). You can use, study, change and share it; if you run a modified
version as a service for others, you must publish your changes under the same license.
