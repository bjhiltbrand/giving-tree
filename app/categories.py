"""Gift categories ("types"), editable in Admin > Settings.

Stored as JSON in the ``categories`` site setting. Each category has a stable ``key`` (what items
store), plus a label, an ornament color from PALETTE and a short icon (usually one emoji). Renaming a
category keeps its key, so existing items follow the new name. ``other`` always exists: it's the
fallback for anything unknown.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

# name: (highlight, light, mid, dark). Lists and dots use highlight/mid/dark; the tree uses light/dark.
PALETTE: dict[str, tuple[str, str, str, str]] = {
    "red": ("#ffd0d0", "#ff6b6b", "#e14a4a", "#a61e1e"),
    "gold": ("#fff5cc", "#ffd85c", "#f5c93d", "#b07d0a"),
    "orange": ("#ffe2cc", "#ffa05c", "#f6904a", "#a5500f"),
    "pink": ("#ffe0f0", "#ff8cc6", "#f06aae", "#a3246a"),
    "purple": ("#ecdcff", "#b58cf0", "#a57ae6", "#5a2f98"),
    "blue": ("#d6efff", "#6cc3ff", "#55b1f2", "#1d5f9e"),
    "teal": ("#d4f7f4", "#5fd6cc", "#2fb8ad", "#16675f"),
    "green": ("#dcf7e4", "#7ddf9f", "#45c276", "#1b6e3d"),
    "silver": ("#ffffff", "#f4f4fa", "#e3e3ee", "#8d8da6"),
}
FALLBACK_KEY = "other"
MAX_CATEGORIES = 20


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    color: str
    icon: str


DEFAULTS = [
    Category("clothing", "Clothing", "red", "👕"),
    Category("toys", "Toys & Games", "gold", "🧸"),
    Category("books", "Books", "purple", "📚"),
    Category("giftcard", "Gift Card", "blue", "💳"),
    Category("household", "Household", "orange", "🏠"),
    Category(FALLBACK_KEY, "Other", "silver", "✨"),
]


def to_json(cats: list[Category]) -> str:
    return json.dumps([asdict(c) for c in cats], ensure_ascii=False)


DEFAULT_JSON = to_json(DEFAULTS)


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:30] or "type"


def clean_icon(icon: str) -> str:
    return (icon or "").strip()[:8]


class CategorySet:
    """The current categories, in display order."""

    def __init__(self, cats: list[Category]):
        self.cats = cats
        self.by_key = {c.key: c for c in cats}

    def __iter__(self):
        return iter(self.cats)

    def __len__(self):
        return len(self.cats)

    def get(self, key: str) -> Category:
        return self.by_key.get(key) or self.by_key[FALLBACK_KEY]

    def label(self, key: str) -> str:
        return self.get(key).label

    def valid(self, key: str) -> str:
        """``key`` if it exists, else the fallback."""
        return key if key in self.by_key else FALLBACK_KEY

    def index(self, key: str) -> int:
        return self.cats.index(self.get(key))

    def lookup(self) -> dict[str, str]:
        """Lowercased key or label -> key, for matching typed-in categories (bulk add)."""
        out = {c.key: c.key for c in self.cats}
        out |= {c.label.lower(): c.key for c in self.cats}
        return out

    def palette(self, cat: Category) -> tuple[str, str, str, str]:
        return PALETTE.get(cat.color, PALETTE["silver"])

    def tree_data(self) -> dict:
        """What tree.js needs to color and label ornaments."""
        return {c.key: {"label": c.label, "icon": c.icon, "colors": list(self.palette(c)[1::2])}
                for c in self.cats}


def parse(raw: str) -> CategorySet:
    """Reads the stored JSON, dropping anything malformed and making sure ``other`` exists."""
    try:
        rows = json.loads(raw or "")
        if not isinstance(rows, list):
            raise ValueError
    except ValueError:
        rows = [asdict(c) for c in DEFAULTS]
    cats: list[Category] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key", "")).strip()
        label = str(row.get("label", "")).strip()
        if not key or not label or key in seen or not re.fullmatch(r"[a-z0-9-]{1,40}", key):
            continue
        color = row.get("color") if row.get("color") in PALETTE else "silver"
        cats.append(Category(key, label[:60], color, clean_icon(str(row.get("icon", ""))) or "✨"))
        seen.add(key)
    if FALLBACK_KEY not in seen:
        cats.append(DEFAULTS[-1])
    return CategorySet(cats)


def from_settings(values: dict[str, str]) -> CategorySet:
    return parse(values.get("categories", ""))


class CategoryError(ValueError):
    pass


def apply_form(current: CategorySet, rows: list[dict], remove: set[str], in_use: dict[str, int]) -> list[Category]:
    """Builds the new list from the settings form.

    ``rows`` are {key, label, color, icon} in display order; a blank key is a new category (skipped if its
    label is blank too). ``remove`` holds keys to delete. Raises CategoryError with a message for the admin.
    """
    out: list[Category] = []
    used_keys: set[str] = set()  # existing keys handled by the form (kept or removed), plus new keys
    for row in rows:
        key = row.get("key", "").strip()
        label = row.get("label", "").strip()[:60]
        color = row.get("color") if row.get("color") in PALETTE else "silver"
        icon = clean_icon(row.get("icon", "")) or "✨"
        if key:
            if key not in current.by_key or key in used_keys:
                continue  # stale or tampered row
            if key in remove:
                if key == FALLBACK_KEY:
                    raise CategoryError(f"“{current.label(key)}” can't be removed; it's used for anything without a type.")
                if in_use.get(key):
                    raise CategoryError(
                        f"“{current.label(key)}” is used by {in_use[key]} item(s). Change their type first, "
                        "or rename this category instead.")
                used_keys.add(key)
                continue
            if not label:
                raise CategoryError("Every category needs a name.")
        else:
            if not label:
                continue
            base = slugify(label)
            key, n = base, 2
            while key in current.by_key or key in used_keys:
                key, n = f"{base}-{n}", n + 1
        out.append(Category(key, label, color, icon))
        used_keys.add(key)
    # A category missing from the form (e.g. added in another tab meanwhile) is kept, never dropped.
    out += [c for c in current if c.key not in used_keys]
    labels =[c.label.lower() for c in out]
    dupes = sorted({lbl for lbl in labels if labels.count(lbl) > 1})
    if dupes:
        raise CategoryError(f"Two categories can't share a name: {', '.join(dupes)}.")
    if len(out) > MAX_CATEGORIES:
        raise CategoryError(f"Up to {MAX_CATEGORIES} categories are supported.")
    # Keep "Other" last so the list and legend end with the catch-all.
    out.sort(key=lambda c: c.key == FALLBACK_KEY)
    return out
