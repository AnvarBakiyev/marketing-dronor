with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix all remaining broken quotes
content = content.replace(
    "status = 'approved'')",
    "status = 'approved'\")"
).replace(
    "'SELECT COUNT(*) as cnt FROM dm_queue WHERE status = 'approved'",
    '"SELECT COUNT(*) as cnt FROM dm_queue WHERE status = \'approved\''
)

with open('command_center/cc_backend.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed!')
