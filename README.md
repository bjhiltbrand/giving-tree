# 🎄 Giving Tree

A small, self-hosted "giving tree" for collecting gifts for local families. Visitors pick an
ornament from a virtual tree and claim it with just their name and email. Organizers build each
family's wish list in an admin area and track every gift from *claimed* to *received*.

- **Visitors:** no account and no password. Name + email, one click, done.
- **Organizers:** families (shown under an alias for privacy), wish lists, quick-add, claim tracking, CSV export.
- **Email:** confirmation with a private "manage" link, reminders before the drop-off deadline, a thank-you once a gift is received, and organizer notifications.
- **Stack:** Python/FastAPI, server-rendered pages, SQLite in a Docker volume, a single container.

## Quick start

```bash
cp .env.example .env        # then edit BASE_URL, SECRET_KEY, ADMIN_PASSWORD and the SMTP settings
docker compose up -d --build
```

Point your reverse proxy at `http://<docker-host>:8000` (change it with `HOST_PORT`). TLS stays on the
proxy. The app trusts `X-Forwarded-*` headers and marks cookies `Secure` when `BASE_URL` is `https://`.

Then open `https://your-site/admin`, log in, and:

1. **Settings:** set the title, welcome text, **drop-off deadline**, drop-off instructions, and the organizer notification email. Use **Send test email** to check SMTP.
2. **Add a family:** give it a public alias ("Family A") and keep real details in *Private notes*.
3. **Add items:** one at a time, or paste a whole list into **Quick add**:
   ```
   Winter coat | Boy, age 7 | Youth M | clothing
   LEGO set | Boy, age 7 | | toys
   $25 grocery gift card | Family | | gift card | 4
   ```

If you leave `ADMIN_PASSWORD` empty, a random password is generated on first start. Find it with
`docker compose logs giving-tree`.

## How claim tracking works (minimal friction)

Each visitor is identified **only by email**. There are no accounts.

| Step | What happens |
| --- | --- |
| Claim | Visitor enters name + email (plus an optional note) next to a short notice explaining what the email is used for. The ornament is reserved immediately. |
| Confirmation email | Contains the gift details, deadline, drop-off instructions and a **private link** (`/my/<token>`) to view their ornaments, release one ("I can't do this one"), report "I dropped it off", or turn reminders off. |
| Remember me | The browser remembers their name and email, so the next claim is one click. Their private page is also linked from "My ornaments" in the header. |
| Lost the link? | "My ornaments" → enter email → the link is emailed to them. It is never shown on screen, so typing someone else's email reveals nothing. |
| Reminders | Sent N days before the deadline (default `7,2`), bundled into one email per person. Skipped if they claimed inside that window. An optional "check-in N days after claiming" is available too. |
| Drop-off | The giver clicks "I dropped it off" (organizers get notified), then an organizer clicks **Received ✓**, which sends the thank-you. Organizers can also mark gifts received directly. |

**Optional stricter mode:** under *Settings → Require email confirmation*, claims are only *held*
until the visitor clicks a link in their email. Unconfirmed holds are released after N hours. This
stops typos and fake addresses but adds a step, so it's off by default. Turn it on if you see junk claims.

Other safeguards: an invisible honeypot field for bots, per-IP rate limits on claiming, emailing
links and admin login, CSRF tokens on every form, and scrypt-hashed admin passwords.

## Email setup

Any SMTP account works. See `.env.example` for complete examples.

**Mailcow (recommended).** Create a mailbox such as `givingtree@yourdomain.com` and, ideally, an
app password for it. Then set `SMTP_HOST=mail.yourdomain.com`, `SMTP_PORT=587`, `SMTP_SECURITY=starttls`,
and use the mailbox address and password as `SMTP_USERNAME`/`SMTP_PASSWORD`. Use the same address in
`MAIL_FROM`. Check that SPF/DKIM/DMARC pass in Mailcow's DNS check, or reminders may land in spam.
Mailcow's *API keys* are for administering the server and aren't needed for sending.

**Gmail.** Turn on 2-Step Verification, create an App Password, and use `smtp.gmail.com:587`
(`starttls`). Personal Gmail is limited to about 500 recipients a day, and the From address is
always your Gmail address.

**Testing without real email.** Leave `SMTP_HOST` empty and emails are only written to the log
(*Admin → Emails*). Or run the bundled Mailpit inbox with `docker compose --profile mailtest up -d`,
set `SMTP_HOST=mailpit`, `SMTP_PORT=1025`, `SMTP_SECURITY=none`, and view mail at `http://<host>:8025`.

## Operations

- **Data:** a single SQLite file in the `giving-tree-data` volume (`/data/giving_tree.db`). Back it up with
  `docker compose exec giving-tree python -c "import sqlite3; sqlite3.connect('/data/giving_tree.db').backup(sqlite3.connect('/data/backup.db'))"`
  and copy it out, or snapshot the volume.
- **Next season:** hide or delete last year's families, update the deadline, and re-open claiming.
  Use *Claims → Export CSV* first if you want a record.
- **Health check:** `GET /healthz`.
- **Scaling:** the app intentionally runs one worker, because the reminder scheduler runs in-process.
  That's plenty for hundreds of visitors.

## Demo data for a test site

To show people what the site looks like, load three sample families with gift lists and a few claims
in different stages (claimed, dropped off, received):

```bash
docker compose exec giving-tree python -m app.seed_demo            # empty site only
docker compose exec giving-tree python -m app.seed_demo --reset    # replace existing families/claims
docker compose exec giving-tree python -m app.seed_demo --no-claims  # families and gifts only
```

- Admin accounts and settings are kept. If no deadline is set, it sets one three weeks out.
- Demo donors use `@example.com` addresses with reminders off, so no emails go to them. Real claims
  made by visitors on the test site behave normally.
- `--reset` refuses to run if the database holds any donor with a real email address, so it can't
  wipe a live site by accident. Once people have tried claiming on the test site, add `--force`.

## Local development

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # (bin/ on macOS/Linux)
DATABASE_URL=sqlite:///./data/dev.db ADMIN_PASSWORD=devpass123 .venv/Scripts/uvicorn app.main:app --reload
```
