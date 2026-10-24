"""SCRUM-47: Chicken Curry contains yogurt, so its allergen is milk, not 'none'.

Updates ONE existing row only. Safe to run more than once.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.database.connection import get_connection  # noqa: E402


def main():
    connection = get_connection()
    if connection is None:
        print("Database connection failed.")
        return
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE dish_dna SET allergens = 'milk' "
            "WHERE name = 'Chicken Curry' "
            "AND (allergens IS NULL OR LOWER(allergens) = 'none')"
        )
        connection.commit()
        print(f"Rows updated: {cursor.rowcount}")
    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    main()