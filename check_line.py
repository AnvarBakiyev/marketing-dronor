with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix broken quotes - use escaped quotes
content = content.replace("role = 'admin''", "role = ''admin''")
content = content.replace("status = 'approved''", "status = ''approved''")
content = content.replace("status = 'rejected''", "status = ''rejected''")
content = content.replace("status = 'ready_to_send''", "status = ''ready_to_send''")

# Alternative: wrap SQL in double quotes
import re

# Find all single-quoted SQL with 'value' inside and fix
def fix_sql_quotes(content):
    # Pattern: 'SQL ... = 'value'' -> "SQL ... = 'value'"
    lines = content.split('\n')
    fixed_lines = []
    for line in lines:
        if "role = 'admin''" in line:
            line = line.replace("'SELECT", '"SELECT').replace("admin'')", "admin'\")")
        fixed_lines.append(line)
    return '\n'.join(fixed_lines)

# Simpler fix - just show line 106
print('Checking line 106...')
lines = content.split('\n')
for i, line in enumerate(lines[100:115], 101):
    print(f'{i}: {line}')

