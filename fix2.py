with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix line 106 specifically
content = content.replace(
    "db_execute(db, 'SELECT COUNT(*) as cnt FROM operators WHERE role = 'admin'')",
    'db_execute(db, "SELECT COUNT(*) as cnt FROM operators WHERE role = \'admin\'")'
)

with open('command_center/cc_backend.py', 'w', encoding='utf-8') as f:
    f.write(content)

# Verify
with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    lines = f.read().split('\n')
    print(f'106: {lines[105]}')
