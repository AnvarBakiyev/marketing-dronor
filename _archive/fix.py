with open('command_center/cc_backend.py', 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace(chr(0x432)+chr(0x402)+chr(0x201c), '-')
with open('command_center/cc_backend.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Size:', len(content))
