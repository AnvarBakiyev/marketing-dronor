#!/usr/bin/env python3
import re

path = 'command_center/cc_backend_new.py'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

print(f'Original size: {len(content)} chars')

# Git conflict markers pattern
old_pattern = '''<<<<<<< Updated upstream
            with conn.cursor() as cur:
                import psycopg2.extras as _extras
                cur2 = conn.cursor(cursor_factory=_extras.RealDictCursor)
                cur2.execute("""
=======
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
>>>>>>> Stashed changes'''

new_code = '''            with conn.cursor() as cur:
                import psycopg2.extras as _extras
                cur2 = conn.cursor(cursor_factory=_extras.RealDictCursor)
                cur2.execute("""'''

if old_pattern in content:
    content = content.replace(old_pattern, new_code)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('FIX 1 SUCCESS: Git conflict resolved')
else:
    print('FIX 1 FAILED: Pattern not found, trying regex...')
    # Try with regex
    pattern = r'<<<<<<<[^\n]*\n.*?>>>>>>>\s*Stashed changes'
    if re.search(pattern, content, re.DOTALL):
        content = re.sub(pattern, new_code, content, flags=re.DOTALL)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        print('FIX 1 SUCCESS via regex')
    else:
        print('No git conflict markers found')

print(f'New size: {len(content)} chars')
