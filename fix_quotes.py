with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the broken quotes - change outer quotes to double
content = content.replace(
    "db_execute(db, 'SELECT COUNT(*) as cnt FROM operators WHERE role = ''admin'')",
    'db_execute(db, "SELECT COUNT(*) as cnt FROM operators WHERE role = \'admin\'")'
)

# Also fix any other ''value'' patterns
content = content.replace("= ''admin''", "= 'admin'")
content = content.replace("= ''approved''", "= 'approved'")
content = content.replace("= ''rejected''", "= 'rejected'")
content = content.replace("= ''ready_to_send''", "= 'ready_to_send'")

with open('command_center/cc_backend.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed quotes!')

# Verify
with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    lines = f.read().split('\n')
    for i, line in enumerate(lines[104:108], 105):
        print(f'{i}: {line}')
