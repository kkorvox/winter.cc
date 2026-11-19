import os, sqlite3, secrets, hashlib, json, random, string, time, re
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
import bcrypt

DB = os.getenv('WINTER_DB', str(Path(__file__).resolve().parent.parent / 'winter.db'))
WORDS = ['void','ghost','chrome','angel','vex','nova','frost','pixel','night','lunar','zero','echo','haze','orbit','drift','glow','shade','storm','azure','bliss','velvet','static','raven','onyx','winter']
RULES = {
 'Discord': (2,32,set(string.ascii_lowercase+string.digits+'._')),
 'Roblox': (3,20,set(string.ascii_letters+string.digits+'_')),
 'Instagram': (1,30,set(string.ascii_letters+string.digits+'._')),
 'TikTok': (2,24,set(string.ascii_letters+string.digits+'._')),
 'Telegram': (5,32,set(string.ascii_letters+string.digits+'_')),
 'Minecraft': (3,16,set(string.ascii_letters+string.digits+'_')),
}
# These are conservative candidate filters, not definitive platform validation.

def now(): return datetime.now(timezone.utc).isoformat()
@contextmanager
def db():
    Path(DB).parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DB,timeout=20)
    c.row_factory=sqlite3.Row
    try:
        yield c
        c.commit()
    finally: c.close()

def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password BLOB NOT NULL, is_admin INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS licenses(id INTEGER PRIMARY KEY, key_hash TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, revoked INTEGER DEFAULT 0, expires_at TEXT, FOREIGN KEY(user_id) REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS searches(id INTEGER PRIMARY KEY, user_id INTEGER, platform TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS results(search_id INTEGER, username TEXT, status TEXT, PRIMARY KEY(search_id,username));
        CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER, platform TEXT, username TEXT, PRIMARY KEY(user_id,platform,username));
        CREATE TABLE IF NOT EXISTS presets(user_id INTEGER, name TEXT, config TEXT, PRIMARY KEY(user_id,name));
        CREATE TABLE IF NOT EXISTS check_state(user_id INTEGER, platform TEXT, previous_search INTEGER, PRIMARY KEY(user_id,platform));
        ''')

def hash_key(key): return hashlib.sha256(key.encode()).hexdigest()
def create_user(username,password,admin=False):
    if not re.fullmatch(r'[a-zA-Z0-9_]{3,32}',username): raise ValueError('Username must be 3-32 letters, numbers or underscores')
    if len(password)<12: raise ValueError('Password must be at least 12 characters')
    with db() as c:
        c.execute('INSERT INTO users(username,password,is_admin) VALUES(?,?,?)',(username,bcrypt.hashpw(password.encode(),bcrypt.gensalt()),int(admin)))
        return c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()['id']
def create_license(user_id,expires_at=None):
    key='WCC-'+secrets.token_urlsafe(24)
    with db() as c: c.execute('INSERT INTO licenses(key_hash,user_id,expires_at) VALUES(?,?,?)',(hash_key(key),user_id,expires_at))
    return key

def auth(username,password,key):
    with db() as c:
        u=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        if not u or not bcrypt.checkpw(password.encode(),u['password']): return None
        lic=c.execute('SELECT * FROM licenses WHERE key_hash=? AND user_id=? AND revoked=0',(hash_key(key),u['id'])).fetchone()
        if not lic or (lic['expires_at'] and lic['expires_at']<now()): return None
        token=secrets.token_urlsafe(32)
        c.execute('INSERT INTO sessions VALUES(?,?,?)',(hash_key(token),u['id'],time.time()+12*3600))
        return token

def current_user(token):
    with db() as c:
        row=c.execute('''SELECT u.id,u.username,u.is_admin FROM sessions s JOIN users u ON u.id=s.user_id
        WHERE s.token_hash=? AND s.expires>? AND EXISTS(SELECT 1 FROM licenses l WHERE l.user_id=u.id AND l.revoked=0 AND (l.expires_at IS NULL OR l.expires_at>?))''',(hash_key(token),time.time(),now())).fetchone()
        return dict(row) if row else None

def candidates(platform,lengths,types,characters,prefix,suffix,count,wordlist=None):
    if platform not in RULES: raise ValueError('Unsupported platform')
    minlen,maxlen,allowed=RULES[platform]
    lengths=[int(x) for x in lengths if str(x).isdigit() and minlen<=int(x)<=maxlen]
    if not lengths: raise ValueError('No valid lengths for this platform')
    if not (1<=count<=100000): raise ValueError('Amount must be 1–100,000')
    pool=''.join(x for k,x in [('Lowercase',string.ascii_lowercase),('Uppercase',string.ascii_uppercase),('Numbers',string.digits),('Underscores','_'),('Periods','.')] if k in characters and x)
    pool=''.join(sorted(set(pool)&allowed))
    if not pool: raise ValueError('No permitted characters selected')
    if any(x not in allowed for x in prefix+suffix): raise ValueError('Prefix or suffix contains unsupported characters')
    if len(prefix)+len(suffix)>max(lengths): raise ValueError('Prefix and suffix exceed selected length')
    types=types or ['Random letters']
    words=[w.strip() for w in (wordlist or WORDS) if isinstance(w,str) and w.strip()]
    words=[w for w in words if all(c in allowed for c in w)] or WORDS
    rng=random.SystemRandom(); seen=set(); out=[]; attempts=0
    consonants='bcdfghjklmnpqrstvwxyz'; vowels='aeiou'
    while len(out)<count and attempts<max(1000,count*35):
        attempts+=1; size=rng.choice(lengths); mid=size-len(prefix)-len(suffix)
        if mid<0: continue
        mode=rng.choice(types)
        if mode=='Numbers only': base=''.join(rng.choices(string.digits,k=mid))
        elif mode in ('Real words / OG','Aesthetic'):
            word=rng.choice(words)
            base=(word[:mid] if len(word)>=mid else word+''.join(rng.choices(pool,k=mid-len(word))))
        elif mode=='Pronounceable': base=''.join(rng.choice(consonants if i%2==0 else vowels) for i in range(mid))
        elif mode=='Letters + numbers':
            mixed=''.join(c for c in pool if c.isalnum())
            base=''.join(rng.choices(mixed or pool,k=mid))
        else: base=''.join(rng.choices(pool,k=mid))
        name=prefix+base+suffix
        if len(name) not in lengths or any(c not in allowed for c in name): continue
        if platform=='Discord': name=name.lower()
        if name not in seen: seen.add(name);out.append(name)
    return out

def start_search(user_id,platform,names):
    if platform not in RULES: raise ValueError('Unsupported platform')
    names=list(dict.fromkeys(names))
    with db() as c:
        prev=c.execute('SELECT previous_search FROM check_state WHERE user_id=? AND platform=?',(user_id,platform)).fetchone()
        skipped=set()
        if prev:
            skipped={r['username'] for r in c.execute('SELECT username FROM results WHERE search_id=? AND status=?',(prev['previous_search'],'available'))}
        cur=c.execute('INSERT INTO searches(user_id,platform,created) VALUES(?,?,?)',(user_id,platform,now()))
        sid=cur.lastrowid
        rows=[{'username':name,'status':'skipped' if name in skipped else 'unknown'} for name in names]
        c.executemany('INSERT INTO results(search_id,username,status) VALUES(?,?,?)',[(sid,r['username'],r['status']) for r in rows])
        c.execute('INSERT INTO check_state(user_id,platform,previous_search) VALUES(?,?,?) ON CONFLICT(user_id,platform) DO UPDATE SET previous_search=excluded.previous_search',(user_id,platform,sid))
    return sid,rows
