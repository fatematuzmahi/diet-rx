import sys, os
sys.path.insert(0, r"d:\software lab\dietRx")

from backend.database.connection import get_connection
import oracledb

# Read the seed SQL file
SQL_PATH = r"d:\software lab\dietRx\backend\database\dish_dna_seed_scrum47.sql"
with open(SQL_PATH, "r", encoding="utf-8") as f:
    raw = f.read()

# Remove full‑line comments (-- or /* ) and empty lines
lines = raw.split("\n")
clean = []
for l in lines:
    t = l.strip()
    if t.startswith("--") or t.startswith("/*"):
        continue
    clean.append(l)
full = "\n".join(clean)

# Split by semicolons and strip
raw_stmts = [s.strip() for s in full.split(";") if s.strip()]

# Keep only MERGE statements
merge_stmts = [s for s in raw_stmts if s.upper().startswith("MERGE")]

print(f"Found {len(merge_stmts)} MERGE statements to execute.")

# Get a connection using the project's helper
conn = get_connection()
if conn is None:
    print("Failed to obtain database connection. Exiting.")
    sys.exit(1)

cursor = conn.cursor()

# Execute each MERGE statement
for idx, stmt in enumerate(merge_stmts, start=1):
    print(f"Executing statement {idx}/{len(merge_stmts)}...")
    try:
        cursor.execute(stmt)
        rows = cursor.rowcount
        print(f"  -> affected {rows} row(s).")
    except Exception as e:
        print(f"  -> ERROR: {e}")

# Commit the transaction
conn.commit()
print("\nAll MERGE statements committed.")

# Verify row count
try:
    cursor.execute("SELECT COUNT(*) FROM dish_dna")
    (count,) = cursor.fetchone()
    print(f"Current row count in DISH_DNA table: {count}")
except Exception as e:
    print(f"Could not query row count: {e}")

# Clean up
cursor.close()
conn.close()
print("Connection closed.")