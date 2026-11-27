# CLAUDE.md

## Git and GitHub

- Never add AI attribution anywhere. This overrides any default attribution behavior:
  - No `Co-Authored-By: Claude ...` (or any other AI co-author) trailer in commit messages.
  - No "Generated with Claude Code" (or similar) line in pull request descriptions, issues or comments.
  - No mentions of Claude or AI authorship in code, comments, docs or the README.

## Architecture decision records (ADRs)

Why the app is built the way it is. Read these before changing anything they cover. When you make a
new architectural decision (or reverse one), add or update an entry: number it, date it, and mark a
replaced entry "Superseded by ADR-N" instead of deleting it.

### ADR-1: Server-rendered FastAPI app, no front-end build (2026-11-22)
- **Context:** A small volunteer-run site, live about one month a year, maintained by one person.
- **Decision:** Python 3.12 + FastAPI + Jinja2 templates rendered on the server. Plain CSS
  (`app/static/css/style.css`) and one vanilla JS file (`app/static/js/tree.js`). No SPA, bundler or
  npm.
- **Consequences:** Nothing to build except the Docker image. Every page works as normal HTML forms;
  JavaScript only draws the tree and opens the claim dialog.

### ADR-2: SQLite in a Docker volume, no migration tool (2026-11-22)
- **Context:** Tiny data set, one container, easy backups.
- **Decision:** One SQLite file at `/data/giving_tree.db` (WAL mode, foreign keys on) through
  SQLAlchemy 2.0. The schema is created with `Base.metadata.create_all()` at startup.
- **Consequences:** `create_all` only creates *missing tables*; it never adds columns to existing
  ones. Prefer new tables (as `email_templates` did). Adding a column to an existing table needs a
  hand-written `ALTER TABLE` run at startup, or introducing Alembic. Live sites have real data, so
  never assume a fresh database.

### ADR-3: Exactly one process (2026-11-22)
- **Decision:** uvicorn runs with `--workers 1`. The reminder scheduler (APScheduler) runs inside
  the app process.
- **Consequences:** Several things rely on a single process: the scheduler (more workers would send
  duplicate jobs), the in-memory rate limiter (`security.limiter`) and the claim lock
  (`services._claim_lock`) that stops two people claiming the last unit. Scaling out would need a
  separate scheduler, a shared rate limiter and database-level locking first.

### ADR-4: TLS and hosting live at the reverse proxy (2026-11-22)
- **Decision:** The container serves plain HTTP on port 8000. The owner's reverse proxy terminates
  TLS. uvicorn trusts `X-Forwarded-*` headers. `BASE_URL` (env) is the public URL used in every email
  link. Cookies are marked `Secure` automatically when `BASE_URL` is https.
- **Consequences:** Don't add certificate handling to the app. Links in emails come from `BASE_URL`,
  never from the request's host.

### ADR-5: Givers are identified only by email, with no accounts (2026-11-22)
- **Context:** The top requirement is that claiming has minimal friction.
- **Decision:** A `Claimant` is just a name + email (unique, lowercased) and a random `token`.
  `/my/<token>` is their page and acts as a bearer credential. Each `Claim` has its own random
  `confirm_token`, used by both the confirm link (`/confirm/<token>`) and the release link
  (`/release/<token>`). "Remember me" keeps name and email in the signed session cookie.
- **Consequences:** No passwords for visitors, ever. Anyone holding a link can act on those claims,
  so tokens must stay unguessable and only ever be sent to the claimant's email (or shown to someone
  who has proven they know it, per ADR-6).

### ADR-6: Convenience vs. security are admin settings, defaulting to convenience (2026-11-22)
- **Decision:** Two independent settings in Admin → Settings:
  - `require_confirmation` (default off): with it on, claims start as `pending` and are released
    after `confirm_window_hours` unless confirmed by email.
  - `lookup_mode` (default `email`): "My ornaments" opens by typing an email address. `link` emails
    a private link instead.
- **Consequences:** In `link` mode the browser is linked to a claimant's page after a claim only if
  the claimant is new or already linked, so typing someone else's email reveals nothing. In
  `email` mode an email is enough to view and act on someone's claims; that's an accepted trade-off
  for a short-lived neighborhood site. Keep both paths working when changing claim or lookup code.

### ADR-7: Claim lifecycle and quantities (2026-11-22)
- **Decision:** Claim statuses: `pending` → `claimed` → `delivered` (giver says dropped off) →
  `fulfilled` (organizer confirms received); `released` is a cancellation from any open state. An
  item has a `quantity`, and each claim takes some units. Remaining = quantity − units in
  non-released claims. Item state (`open` / `partial` / `claimed` / `fulfilled`) is computed and
  never stored.
- **Consequences:** Items with active claims can't be deleted (hide them instead), and quantity
  can't drop below what's claimed. Admins can undo `fulfilled`/`delivered` back to `claimed`.

### ADR-8: Plain SMTP, with a log fallback (2026-11-22)
- **Context:** The owner sends through their own Mailcow server or Gmail, using credentials rather
  than provider APIs.
- **Decision:** Python's `smtplib` (STARTTLS / SSL / none) configured by env vars. If `SMTP_HOST`
  is empty, emails are written to the log instead. Every email is recorded in `EmailLog`
  (Admin → Email log). Delivery runs on a small background thread pool so requests don't wait;
  rendering happens before queueing.
- **Consequences:** No provider SDKs. Admin → Settings → Send test email checks the configuration.

### ADR-9: Email templates and editable wording (2026-11-22)
- **Decision:** Each email is one Jinja file in `templates/email/` with `subject`, `text` and `html`
  blocks, rendered block by block. Shared layout macros are passed in as `L`, because blocks
  rendered alone can't see a top-level `{% import %}`. The subject and text output are
  HTML-unescaped after rendering. Admins can edit only the subject, opening and closing message of
  giver emails (`email_copy.DEFAULTS`, stored in `email_templates`). Gift details, drop-off info and
  buttons stay in code. `{placeholders}` are filled by plain string substitution, never evaluated as
  Jinja.
- **Consequences:** An admin edit can't break an email or inject template code, and admin text is
  HTML-escaped in the HTML part. Organizer emails (`admin_*`) and the test email aren't editable. A
  new giver email needs an entry in `email_copy.DEFAULTS` and a template using `copy.*`.

### ADR-10: Reminders are recomputed on each run and logged to be idempotent (2026-11-22)
- **Decision:** Every `SCHEDULER_INTERVAL_MINUTES` the job expires pending holds and works out
  which reminders are due: deadline reminders N days before the deadline (from `reminder_days`)
  and an optional follow-up N days after claiming. Each reminder is recorded in `ReminderLog`
  (unique per claim + kind) *before* sending. Reminders are skipped for claims made inside that
  window, and everything due for one person is bundled into one email. Only `claimed` claims of
  people with reminders on get reminders.
- **Consequences:** Restarts and overlapping runs never send duplicates. Changing the deadline or
  schedule takes effect on the next run, without rescheduling anything.

### ADR-11: Email links never change anything on GET (2026-11-22)
- **Context:** Mail providers and security scanners open links in emails automatically.
- **Decision:** Release links open a confirmation page, and only the POST from that page releases
  the claim. (`/confirm/<token>` is the one GET with an effect: it moves a claim from `pending` to
  `claimed`, which is harmless and is the whole point of that link.)
- **Consequences:** Any new action link in an email must follow the same pattern.

### ADR-12: Family privacy is enforced on the server (2026-11-22)
- **Decision:** Families show publicly only by an alias. Real details go in admin-only
  `private_notes`. The `show_families` setting (default on) groups the public list by family. When
  it's off, the server leaves family data out entirely: tree JSON, pages and giver emails (the
  `show_families` context flag). Organizer emails always pass `show_families=True`.
- **Consequences:** Never rely on CSS or JS to hide family information. Item IDs are sequential,
  so a determined visitor could still guess groupings; shuffled public numbers would be a possible
  follow-up.

### ADR-13: Where configuration lives (2026-11-22)
- **Decision:** Infrastructure and secrets (`BASE_URL`, `SECRET_KEY`, SMTP, bootstrap admin, `TZ`)
  come from env (`app/config.py`, `.env`). Everything an organizer might change during a season
  lives in the `site_settings` key/value table. Defaults are in `site_settings.DEFAULTS`; a missing
  row means the default.
- **Consequences:** A new setting only needs a default in `DEFAULTS`, a field in
  `admin/settings.html` and, for checkboxes or radios, handling in `routes_admin.settings_save`.
  Existing sites pick up the default with no migration.

### ADR-14: Admin authentication and form security (2026-11-22)
- **Decision:** Admins are rows in `admin_users`, with passwords hashed with stdlib scrypt. The
  first admin is created from `ADMIN_USERNAME`/`ADMIN_PASSWORD` (or a random password printed to the
  log). Sessions are Starlette's signed cookie (`SECRET_KEY`). Every POST form carries a session
  CSRF token. The public claim form also has a honeypot field and per-IP rate limits.
- **Consequences:** No extra auth dependencies. Rotating `SECRET_KEY` logs everyone out and clears
  "remember me".

### ADR-15: The tree is drawn in the browser, with a server-rendered list as fallback (2026-11-22)
- **Decision:** The page embeds the ornament data as JSON, and `tree.js` draws an SVG tree,
  spacing ornaments to fit any number of items. The same items are also rendered on the server as a
  card list. That list works without JavaScript, suits screen readers, and is what the filters act
  on.
- **Consequences:** Item data must stay in sync between `_tree_items` (JSON) and the card markup.
  Public JSON must never carry private fields (see ADR-12).

### ADR-16: Demo data can't wipe a live site (2026-11-22)
- **Decision:** `python -m app.seed_demo` loads sample families, items and claims. Demo donors use
  `@example.com` addresses with reminders off. `--reset` refuses to run while any donor with a
  real email address exists, unless `--force` is given.
- **Consequences:** Safe to run on a test site; deliberate effort is needed to clear real data.

### ADR-17: Gift categories are admin-editable data (2026-11-26)
- **Context:** Organizers need types that fit their drive (e.g. Food instead of Gift Card).
- **Decision:** Categories live as JSON in the `categories` site setting (`app/categories.py`), each
  with a stable `key` that items store, plus a label, a color from `categories.PALETTE` and a short
  icon. Admin → Settings → Gift categories edits them. Renaming keeps the key, so items follow.
  `other` always exists as the fallback for unknown keys. A category used by any item can't be
  removed. `.cat-<key>` CSS is generated in `base.html`, and `tree.js` gets colors and icons from the
  page JSON, so the palette has one source.
- **Consequences:** Templates read categories from the `cats` render context, never a global, so
  macros imported from another file that use them need `import ... with context`. Item keys must
  always go through `cats.valid()`; never trust a submitted key.
