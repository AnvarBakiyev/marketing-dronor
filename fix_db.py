import re

with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    content = f.read()

# New get_db function that supports both SQLite and PostgreSQL
new_get_db = '''def get_db():
    """Get database connection - PostgreSQL on Railway, SQLite locally."""
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(database_url)
        return conn
    else:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn'''

# Replace old get_db
content = re.sub(
    r'def get_db\(\):.*?return conn',
    new_get_db,
    content,
    flags=re.DOTALL,
    count=1
)

# Fix SQL placeholders: ? -> %s for PostgreSQL compatibility
# Only in execute statements
content = re.sub(r"execute\('([^']*)\?([^']*)'", lambda m: "execute('" + m.group(1).replace('?', '%s') + "%s" + m.group(2).replace('?', '%s') + "'", content)

with open('command_center/cc_backend.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done! Size:', len(content))
