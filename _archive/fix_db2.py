with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix get_db to return a wrapper that works like SQLite
old_get_db = '''def get_db():
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

new_get_db = '''def get_db():
    """Get database connection - PostgreSQL on Railway, SQLite locally."""
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(database_url)
        conn.cursor_factory = RealDictCursor
        return conn
    else:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn

def db_execute(db, query, params=()):
    """Execute query on both SQLite and PostgreSQL."""
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        cur = db.cursor()
        cur.execute(query, params)
        return cur
    else:
        return db.execute(query, params)'''

content = content.replace(old_get_db, new_get_db)

# Replace db.execute with db_execute
content = content.replace('cursor = db.execute(', 'cursor = db_execute(db, ')
content = content.replace('db.execute(', 'db_execute(db, ')

with open('command_center/cc_backend.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done! Size:', len(content))
