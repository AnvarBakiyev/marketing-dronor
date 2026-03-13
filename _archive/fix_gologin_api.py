#!/usr/bin/env python3
"""Add GoLogin Cloud API support to check_connections()"""

path = 'command_center/cc_backend_new.py'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

print(f'Original size: {len(content)} chars')

# Old check_connections function
old_func = '''def check_connections():
    """Check external service connections."""
    # Check GoLogin
    gologin_ok = False
    try:
        import requests
        r = requests.get('http://localhost:36912/browser/v2', timeout=2)
        gologin_ok = r.status_code == 200
    except: pass
    
    # Check Twitter API config
    twitter_ok = bool(os.environ.get('TWITTER_BEARER_TOKEN'))
    
    return jsonify({
        'gologin': gologin_ok,
        'twitter': twitter_ok
    })'''

# New check_connections with GoLogin Cloud API support
new_func = '''def check_connections():
    """Check external service connections."""
    # Check GoLogin - try Cloud API first, then local
    gologin_ok = False
    gologin_mode = 'none'
    gologin_profiles = 0
    try:
        import requests
        gologin_token = os.environ.get('GOLOGIN_API', '')
        if gologin_token:
            # Try Cloud API first
            headers = {'Authorization': f'Bearer {gologin_token}'}
            r = requests.get('https://api.gologin.com/browser/v2', headers=headers, timeout=5)
            if r.status_code == 200:
                gologin_ok = True
                gologin_mode = 'cloud'
                data = r.json()
                gologin_profiles = len(data.get('profiles', []))
        if not gologin_ok:
            # Fallback to local GoLogin
            r = requests.get('http://localhost:36912/browser/v2', timeout=2)
            if r.status_code == 200:
                gologin_ok = True
                gologin_mode = 'local'
    except: pass
    
    # Check Twitter API config
    twitter_ok = bool(os.environ.get('TWITTERAPI_IO_KEY') or os.environ.get('TWITTER_BEARER_TOKEN'))
    
    # Check Anthropic API
    anthropic_ok = bool(os.environ.get('ANTHROPIC_API_KEY'))
    
    # Check Database
    db_ok = False
    try:
        from infra.db import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
                db_ok = True
    except: pass
    
    return jsonify({
        'gologin': gologin_ok,
        'gologin_mode': gologin_mode,
        'gologin_profiles': gologin_profiles,
        'twitter': twitter_ok,
        'anthropic': anthropic_ok,
        'database': db_ok
    })'''

if old_func in content:
    content = content.replace(old_func, new_func)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('SUCCESS: GoLogin Cloud API support added to check_connections()')
else:
    print('ERROR: Could not find check_connections function')
    print('Searching for partial match...')
    if 'def check_connections()' in content:
        print('Function exists but format differs')

print(f'New size: {len(content)} chars')
