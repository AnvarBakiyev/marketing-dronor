with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix lines 451, 456, 462 - change outer single quotes to double
content = content.replace(
    "'UPDATE dm_queue SET status = 'approved', reviewed_at = %s WHERE id = %s',",
    '"UPDATE dm_queue SET status = \'approved\', reviewed_at = %s WHERE id = %s",'
)

content = content.replace(
    "'UPDATE dm_queue SET status = 'rejected', reviewed_at = %s WHERE id = %s',",
    '"UPDATE dm_queue SET status = \'rejected\', reviewed_at = %s WHERE id = %s",'
)

content = content.replace(
    "'UPDATE dm_queue SET status = 'ready_to_send' WHERE id = %s',",
    '"UPDATE dm_queue SET status = \'ready_to_send\' WHERE id = %s",'
)

with open('command_center/cc_backend.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed 451, 456, 462!')

# Verify
with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    lines = f.read().split('\n')
    for i in [450, 455, 461]:
        print(f'{i+1}: {lines[i]}')
