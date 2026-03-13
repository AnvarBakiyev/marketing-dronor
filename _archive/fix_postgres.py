with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Fix double quotes in SQL values -> single quotes
content = content.replace('role = "admin"', "role = 'admin'")
content = content.replace('status = "approved"', "status = 'approved'")
content = content.replace('status = "rejected"', "status = 'rejected'")
content = content.replace('status = "ready_to_send"', "status = 'ready_to_send'")

# 2. Fix ? -> %s for PostgreSQL (only in SQL strings)
import re
def fix_placeholders(match):
    return match.group(0).replace('?', '%s')

content = re.sub(r"execute\([^)]+\)", fix_placeholders, content)

# 3. Fix AUTOINCREMENT -> SERIAL for PostgreSQL
content = content.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY')

with open('command_center/cc_backend.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed all PostgreSQL issues!')
