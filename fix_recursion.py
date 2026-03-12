with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the recursive call
content = content.replace(
    'return db_execute(db, query, params)',
    'return db.execute(query, params)'
)

with open('command_center/cc_backend.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed!')
