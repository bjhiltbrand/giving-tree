"""Load demo data for a test/preview site: three families, their gift lists, and a few sample claims.

Run inside the container:

    docker compose exec giving-tree python -m app.seed_demo            # load into an empty site
    docker compose exec giving-tree python -m app.seed_demo --reset    # wipe families/claims, then load
    docker compose exec giving-tree python -m app.seed_demo --reset --force   # also delete real donors
    docker compose exec giving-tree python -m app.seed_demo --no-claims

Admin accounts and site settings are kept. If no drop-off deadline is set, one is set three weeks out
so the deadline banner and reminder settings have something to show.

Demo donors use @example.com addresses with reminders turned off, so the background job never emails
them. No emails are sent while seeding.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta

from . import site_settings as ss
from .db import Base, SessionLocal, engine
from .models import Claim, Claimant, EmailLog, Family, Item, ReminderLog, utcnow
from .templating import LOCAL_TZ

DEMO_DOMAIN = "@example.com"

# (alias, public description, private notes, [items])
# item: (title, recipient, category, size, quantity, description)
FAMILIES = [
    (
        "The Evergreen Family",
        "Mom and three kids (ages 4, 8 and 12). They just moved into a new apartment.",
        "DEMO DATA. Contact through the school counselor. Deliver to the front office.",
        [
            ("Winter coat", "Girl, age 8", "clothing", "Youth M (10/12)", 1, "Purple or teal if possible."),
            ("Snow boots", "Girl, age 8", "clothing", "Youth 3", 1, ""),
            ("LEGO Classic brick set", "Boy, age 12", "toys", "", 1, "Any medium or large set."),
            ("Graphic novels", "Boy, age 12", "books", "", 2, "He loves adventure and fantasy series."),
            ("Pajamas", "Boy, age 4", "clothing", "4T", 2, "Dinosaurs or trucks."),
            ("Picture books", "Boy, age 4", "books", "", 3, ""),
            ("Grocery gift card", "Family", "giftcard", "", 2, "$50 each, any local grocery store."),
        ],
    ),
    (
        "The Holly Family",
        "Grandparents raising a baby and a 6-year-old.",
        "DEMO DATA. Pick-up arranged with Grandma; call before delivering.",
        [
            ("Diapers", "Baby, 10 months", "household", "Size 4", 3, "Any brand. One box each."),
            ("Stacking and sorting toy", "Baby, 10 months", "toys", "", 1, ""),
            ("Bike helmet", "Boy, age 6", "other", "Child S", 1, "Red is his favorite color."),
            ("Art supply kit", "Boy, age 6", "toys", "", 1, "Crayons, markers and a sketch pad."),
            ("Warm socks", "Grandparents", "clothing", "Adult M", 4, ""),
            ("Gas gift card", "Family", "giftcard", "", 1, "$40."),
        ],
    ),
    (
        "The Juniper Family",
        "Dad and two teenagers (ages 14 and 16).",
        "DEMO DATA. Dad works nights; leave gifts with the building manager.",
        [
            ("Hoodie", "Girl, age 14", "clothing", "Adult S", 1, "Plain, neutral colors."),
            ("Headphones", "Girl, age 14", "other", "", 1, "Over-ear, wired is fine."),
            ("Basketball", "Boy, age 16", "toys", "Size 7", 1, ""),
            ("Running shoes", "Boy, age 16", "clothing", "Men's 10.5", 1, ""),
            ("Board game for the family", "Family", "toys", "", 1, "Something for 3+ players."),
            ("Bath towel set", "Family", "household", "", 2, ""),
        ],
    ),
]

# (name, email)
DONORS = [
    ("Sam Rivera", "sam.rivera" + DEMO_DOMAIN),
    ("Priya Patel", "priya.patel" + DEMO_DOMAIN),
    ("Jordan Lee", "jordan.lee" + DEMO_DOMAIN),
]

# (family index, item title, donor index, quantity, status, days ago, note)
CLAIMS = [
    (0, "Winter coat", 0, 1, "claimed", 3, "Found a teal one!"),
    (0, "LEGO Classic brick set", 1, 1, "fulfilled", 12, ""),
    (0, "Grocery gift card", 2, 1, "delivered", 6, ""),
    (1, "Diapers", 0, 1, "claimed", 2, ""),
    (1, "Bike helmet", 2, 1, "fulfilled", 9, "Matching red bell included."),
    (2, "Basketball", 1, 1, "delivered", 4, ""),
    (2, "Bath towel set", 2, 2, "claimed", 1, ""),
]


def _counts(db) -> dict[str, int]:
    return {
        "families": db.query(Family).count(),
        "claims": db.query(Claim).count(),
        "claimants": db.query(Claimant).count(),
    }


def _real_claimants(db) -> int:
    return db.query(Claimant).filter(~Claimant.email.like("%" + DEMO_DOMAIN)).count()


def reset(db) -> None:
    db.query(ReminderLog).delete()
    db.query(Claim).delete()
    db.query(Claimant).delete()
    db.query(Item).delete()
    db.query(Family).delete()
    db.query(EmailLog).delete()
    db.commit()


def load(db, with_claims: bool = True) -> None:
    families: list[Family] = []
    for f_order, (alias, desc, notes, items) in enumerate(FAMILIES):
        fam = Family(alias=alias, description=desc, private_notes=notes, sort_order=f_order)
        for i_order, (title, recipient, category, size, qty, idesc) in enumerate(items):
            fam.items.append(Item(title=title, recipient=recipient, category=category, size=size,
                                  quantity=qty, description=idesc, sort_order=i_order))
        db.add(fam)
        families.append(fam)
    db.flush()

    if with_claims:
        donors = []
        for name, email in DONORS:
            # Reminders off: the scheduler must never email these made-up addresses.
            donor = Claimant(name=name, email=email, reminders_enabled=False)
            db.add(donor)
            donors.append(donor)
        db.flush()

        now = utcnow()
        for fam_idx, title, donor_idx, qty, status, days_ago, note in CLAIMS:
            item = next(i for i in families[fam_idx].items if i.title == title)
            created = now - timedelta(days=days_ago, hours=2)
            claim = Claim(item_id=item.id, claimant_id=donors[donor_idx].id, quantity=qty, status=status,
                          note=note, created_at=created, confirmed_at=created)
            if status in ("delivered", "fulfilled"):
                claim.delivered_at = created + timedelta(days=days_ago // 2 or 1)
            if status == "fulfilled":
                claim.fulfilled_at = claim.delivered_at + timedelta(hours=20)
            db.add(claim)

    db.commit()

    values = ss.get_all(db)
    if not ss.deadline(values):
        deadline = (datetime.now(LOCAL_TZ).date() + timedelta(days=21)).isoformat()
        ss.save(db, {"deadline": deadline})
        print(f"Set the drop-off deadline to {deadline} (change it in Admin > Settings).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seed_demo", description=__doc__.split("\n\n")[0])
    parser.add_argument("--reset", action="store_true",
                        help="delete all families, gifts, claims, donors and the email log first")
    parser.add_argument("--force", action="store_true",
                        help="allow --reset even when donors with real email addresses exist")
    parser.add_argument("--no-claims", action="store_true", help="load families and gifts only")
    args = parser.parse_args(argv)

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        before = _counts(db)
        if args.reset:
            real = _real_claimants(db)
            if real and not args.force:
                print(f"Refusing to reset: {real} donor(s) with real email addresses are in this database. "
                      "If this is a test site, add --force to delete them too.", file=sys.stderr)
                return 1
            reset(db)
            print("Removed {families} families, {claims} claims and {claimants} donors.".format(**before))
        elif before["families"]:
            print(f"This site already has {before['families']} families. "
                  "Run with --reset to replace them with demo data.", file=sys.stderr)
            return 1

        load(db, with_claims=not args.no_claims)
        after = _counts(db)
        items = db.query(Item).count()
        print(f"Loaded {after['families']} families, {items} gifts and {after['claims']} sample claims.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
