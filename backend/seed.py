from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

# Make sibling modules importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import (  # noqa: E402
    Grocery,
    GroceryAlias,
    GrocerySubstitute,
    SessionLocal,
    init_db,
)


# ---------------------------------------------------------------------------
# Source data (mirrors the user's reference script)
# ---------------------------------------------------------------------------
CATEGORIES: dict[str, list[str]] = {
    "Fruits": ["kg", "piece"],
    "Vegetables": ["kg"],
    "Dairy": ["liter", "pack", "kg"],
    "Grains": ["kg"],
    "Meat": ["kg"],
    "Bakery": ["piece", "pack"],
    "Baking": ["kg"],
    "Essentials": ["liter", "kg"],
}

BASE_ITEMS: list[str] = [
    "Apple", "Banana", "Orange", "Milk", "Bread", "Eggs", "Rice",
    "Chicken", "Beef", "Potato", "Tomato", "Onion", "Sugar",
    "Butter", "Cheese", "Oats", "Flour", "Yogurt", "Carrot", "Cucumber",
]

ALIASES_MAP: dict[str, list[str]] = {
    "Milk": ["doodh", "milk packet"],
    "Rice": ["chawal", "basmati rice"],
    "Sugar": ["cheeni", "sugar crystals"],
    "Bread": ["loaf bread", "pav"],
    "Eggs": ["ande", "egg tray"],
    "Butter": ["makhan"],
    "Potato": ["aloo"],
    "Tomato": ["tamatar"],
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def generate(n: int = 120, seed: int = 42) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (groceries, aliases, substitutes) - lists of plain dicts.

    Deterministic for a given (n, seed) so tests stay stable.
    """
    rng = random.Random(seed)

    groceries: list[dict] = []
    for i in range(1, n + 1):
        base = rng.choice(BASE_ITEMS)
        # Suffix the running id so names stay unique even across duplicate bases.
        name = f"{base} {i}"
        category = rng.choice(list(CATEGORIES.keys()))
        unit = rng.choice(CATEGORIES[category])
        price = round(rng.uniform(0.5, 20.0), 2)
        stock = rng.randint(10, 300)
        groceries.append(
            {
                "id": i,
                "name": name,
                "base": base,
                "category": category,
                "unit": unit,
                "price": price,
                "stock": stock,
            }
        )

    # Aliases: dedup per grocery so we don't insert the same one twice.
    aliases: list[dict] = []
    for g in groceries:
        a_set: set[str] = set(ALIASES_MAP.get(g["base"], []))
        a_set.add(g["base"].lower())
        a_set.add(f"{g['base']} fresh".lower())
        for alias in sorted(a_set):
            aliases.append({"grocery_id": g["id"], "alias": alias})

    # Substitutes: 2 per grocery, no self-links, no duplicate pairs.
    substitute_pairs: set[tuple[int, int]] = set()
    for i in range(1, n + 1):
        added = 0
        attempts = 0
        while added < 2 and attempts < 20:
            attempts += 1
            j = rng.randint(1, n)
            if j == i:
                continue
            pair = (i, j)
            if pair in substitute_pairs:
                continue
            substitute_pairs.add(pair)
            added += 1

    substitutes = [
        {"grocery_id": a, "substitute_id": b} for (a, b) in sorted(substitute_pairs)
    ]
    return groceries, aliases, substitutes


# ---------------------------------------------------------------------------
# DB insert
# ---------------------------------------------------------------------------
def seed(reset: bool = False, dump_sql: bool = False, count: int = 120) -> None:
    init_db()
    db = SessionLocal()
    try:
        if reset:
            db.query(GrocerySubstitute).delete()
            db.query(GroceryAlias).delete()
            db.query(Grocery).delete()
            db.commit()
            print("Cleared existing grocery data.")

        existing = db.query(Grocery).count()
        if existing > 0 and not reset:
            print(
                f"Already seeded ({existing} groceries). "
                f"Use `python seed.py --reset` to reseed."
            )
            if dump_sql:
                _dump_sql(*generate(count))
            return

        groceries, aliases, substitutes = generate(count)

        db.bulk_insert_mappings(
            Grocery,
            [
                {k: g[k] for k in ("id", "name", "category", "unit", "price", "stock")}
                for g in groceries
            ],
        )
        db.bulk_insert_mappings(GroceryAlias, aliases)
        db.bulk_insert_mappings(GrocerySubstitute, substitutes)
        db.commit()

        print(
            f"Seeded {len(groceries)} groceries, "
            f"{len(aliases)} aliases, {len(substitutes)} substitutes."
        )

        if dump_sql:
            _dump_sql(groceries, aliases, substitutes)
    finally:
        db.close()


def _dump_sql(
    groceries: list[dict], aliases: list[dict], substitutes: list[dict]
) -> None:
    """Write a human-readable .sql file of the same data for inspection."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "seed.sql"

    def esc(s: str) -> str:
        return s.replace("'", "''")

    with open(path, "w", encoding="utf-8") as f:
        f.write("-- Auto-generated by seed.py - do not edit by hand.\n")
        f.write("\n-- GROCERIES\n")
        for g in groceries:
            f.write(
                f"INSERT INTO groceries (id, name, category, unit, price, stock) "
                f"VALUES ({g['id']}, '{esc(g['name'])}', '{esc(g['category'])}', "
                f"'{esc(g['unit'])}', {g['price']}, {g['stock']});\n"
            )
        f.write("\n-- ALIASES\n")
        for a in aliases:
            f.write(
                f"INSERT INTO grocery_aliases (grocery_id, alias) "
                f"VALUES ({a['grocery_id']}, '{esc(a['alias'])}');\n"
            )
        f.write("\n-- SUBSTITUTES\n")
        for s in substitutes:
            f.write(
                f"INSERT INTO grocery_substitutes (grocery_id, substitute_id) "
                f"VALUES ({s['grocery_id']}, {s['substitute_id']});\n"
            )
    print(f"Wrote SQL dump: {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the grocery catalog.")
    parser.add_argument("--reset", action="store_true", help="Wipe grocery tables before seeding.")
    parser.add_argument("--dump-sql", action="store_true", help="Also write data/seed.sql.")
    parser.add_argument("--count", type=int, default=120, help="Number of grocery items (default 120).")
    args = parser.parse_args()
    seed(reset=args.reset, dump_sql=args.dump_sql, count=args.count)


if __name__ == "__main__":
    main()
