import sys, os, traceback

# --- configuration -----------------------------------------------------------
SQL_PATH = r'D:/software lab/dietRx/backend/database/dish_dna_scrum47.sql'
# ---------------------------------------------------------------------------

# read the SQL file
with open(SQL_PATH, 'r') as f:
    raw = f.read()

# remove full‑line comments (-- or /* )
lines = raw.split('\n')
clean = []
for l in lines:
    t = l.strip()
    if t.startswith('--') or t.startswith('/*'):
        continue
    clean.append(l)
full = '\n'.join(clean)

# split by semicolons and strip
raw_stmts = [s.strip() for s in full.split(';') if s.strip()]

# --- validation: exactly 4 statements --------------------------------------
if len(raw_stmts) != 4:
    sys.exit('need exactly 4 statements; found ' + str(len(raw_stmts)))

# first must be CREATE TABLE
if not raw_stmts[0].upper().startswith('CREATE TABLE'):
    sys.exit('first statement must be CREATE TABLE')

# remaining 3 must be CREATE INDEX or CREATE UNIQUE INDEX
expected_index_names = []
for i in range(1, 4):
    stmt_upper = raw_stmts[i].upper()
    if not (stmt_upper.startswith('CREATE INDEX') or stmt_upper.startswith('CREATE UNIQUE INDEX')):
        sys.exit('statement ' + str(i+1) + ' must be CREATE INDEX or CREATE UNIQUE INDEX')
    # extract index name: "CREATE INDEX idx_name ON table (col)" -> idx_name
    parts = raw_stmts[i].split()
    idx_name = None
    for j, p in enumerate(parts):
        if p.upper() == 'INDEX' or p.upper() == 'UNIQUE':
            idx_name = parts[j+1]
            break
    if idx_name is None:
        sys.exit('cannot parse index name from statement ' + str(i+1))
    expected_index_names.append(idx_name)

# --- database connection -----------------------------------------------------
from backend.database.connection import get_connection

conn = None
cur = None

try:
    conn = get_connection()
    cur = conn.cursor()

    # abort if DISH_DNA already exists
    cur.execute('SELECT COUNT(*) FROM user_tables WHERE table_name = :n', n='DISH_DNA')
    if cur.fetchone()[0] > 0:
        sys.exit('DISH_DNA already exists - abort')

    # execute statements sequentially
    for i, s in enumerate(raw_stmts):
        print('executing statement ' + str(i+1) + '/4...')
        try:
            cur.execute(s)
            print('ok')
        except Exception as e:
            print('ERROR on statement ' + str(i+1) + ': ' + str(e))
            traceback.print_exc()
            sys.exit(1)

    # --- verification -------------------------------------------------------
    # 1) verify DISH_DNA exists
    cur.execute('SELECT COUNT(*) FROM user_tables WHERE table_name = :n', n='DISH_DNA')
    tbl_cnt = cur.fetchone()[0]
    if tbl_cnt != 1:
        sys.exit('TABLE_COUNT=' + str(tbl_cnt) + '; expected 1')

    # 2) verify expected indexes are present; allow extra Oracle-generated indexes
    cur.execute('SELECT index_name FROM user_indexes WHERE table_name = :n', n='DISH_DNA')
    actual_index_names = [r[0] for r in cur.fetchall()]
    missing_indexes = set(expected_index_names) - set(actual_index_names)
    if missing_indexes:
        sys.exit('Missing expected indexes: ' + ', '.join(sorted(missing_indexes)))

    # print both sets for visibility
    print('Expected indexes: ' + ', '.join(sorted(expected_index_names)))
    print('Actual indexes: ' + ', '.join(sorted(actual_index_names)))

    print('migration applied successfully')

finally:
    if cur is not None:
        cur.close()
    if conn is not None:
        conn.close()