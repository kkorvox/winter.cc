import argparse, getpass
from app.core import *
init()
p=argparse.ArgumentParser(description='winter.cc admin commands')
s=p.add_subparsers(dest='command',required=True)
a=s.add_parser('create-user');a.add_argument('username');a.add_argument('--admin',action='store_true')
a=s.add_parser('issue-license');a.add_argument('username');a.add_argument('--expires',help='ISO UTC date, e.g. 2027-01-01T00:00:00+00:00')
a=s.add_parser('revoke-license');a.add_argument('key',help='Original license key')
a=s.add_parser('list-users')
a=s.add_parser('list-licenses');a.add_argument('username')
a=p.parse_args()
if a.command=='create-user':
    pw=getpass.getpass('Password (12+ chars): ')
    print('Created user ID:',create_user(a.username,pw,a.admin))
elif a.command=='issue-license':
    with db() as c: u=c.execute('SELECT id FROM users WHERE username=?',(a.username,)).fetchone()
    if not u: p.error('Unknown user')
    print('LICENSE KEY (save now; cannot retrieve later):',create_license(u['id'],a.expires))
elif a.command=='revoke-license':
    with db() as c:
        cur=c.execute('UPDATE licenses SET revoked=1 WHERE key_hash=?',(hash_key(a.key),))
        print('Revoked:',bool(cur.rowcount))
elif a.command=='list-users':
    with db() as c:
        for r in c.execute('SELECT id,username,is_admin FROM users'): print(dict(r))
elif a.command=='list-licenses':
    with db() as c:
        for r in c.execute('SELECT l.id,l.revoked,l.expires_at FROM licenses l JOIN users u ON u.id=l.user_id WHERE u.username=?',(a.username,)): print(dict(r))
