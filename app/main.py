import json, os, csv, io
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header, Depends, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .core import *

init()
setup_owner()
app=FastAPI(title='winter.cc private API',docs_url=None,redoc_url=None,openapi_url=None)
ROOT=Path(__file__).resolve().parent.parent
class Login(BaseModel): username:str; password:str; license_key:str
class Generate(BaseModel):
    platform:str='Discord'; lengths:list[int]=Field(default_factory=lambda:[3,4,5]); types:list[str]=Field(default_factory=lambda:['Random letters']); characters:list[str]=Field(default_factory=lambda:['Lowercase','Numbers']); prefix:str=''; suffix:str=''; amount:int=Field(default=100,ge=1,le=100000); wordlist:list[str]|None=None
class Check(BaseModel): platform:str; usernames:list[str]=Field(max_length=100000)
class Mark(BaseModel): search_id:int; username:str; status:str
class Favorite(BaseModel): platform:str; username:str
class Preset(BaseModel): name:str; config:dict

def require(authorization:str|None=Header(None)):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Login required')
    u=current_user(authorization[7:])
    if not u: raise HTTPException(401,'Session expired or license revoked')
    return u
@app.post('/api/login')
def login(body:Login):
    token=auth(body.username,body.password,body.license_key)
    if not token: raise HTTPException(401,'Invalid credentials or license')
    return {'token':token}
@app.get('/api/me')
def me(u=Depends(require)): return u
@app.post('/api/logout')
def logout(authorization:str|None=Header(None),u=Depends(require)):
    with db() as c: c.execute('DELETE FROM sessions WHERE token_hash=?',(hash_key(authorization[7:]),))
    return {'ok':True}
@app.get('/api/platforms')
def platforms(u=Depends(require)):
    return {k:{'min':v[0],'max':v[1],'characters':''.join(sorted(v[2])),'checking':'manual'} for k,v in RULES.items()}
@app.post('/api/generate')
def generate(b:Generate,u=Depends(require)):
    try: names=candidates(b.platform,b.lengths,b.types,b.characters,b.prefix,b.suffix,b.amount,b.wordlist)
    except ValueError as e: raise HTTPException(400,str(e))
    return {'usernames':names,'requested':b.amount,'generated':len(names),'platform':b.platform}
@app.post('/api/check')
def check(b:Check,u=Depends(require)):
    if len(b.usernames)>100000: raise HTTPException(400,'Too many names')
    try: sid,rows=start_search(u['id'],b.platform,b.usernames)
    except ValueError as e: raise HTTPException(400,str(e))
    return {'search_id':sid,'results':rows,'message':'Manual verification required; unknown is not available.'}
@app.post('/api/mark')
def mark(b:Mark,u=Depends(require)):
    if b.status not in ('available','claimed','unknown'): raise HTTPException(400,'Invalid status')
    with db() as c:
        row=c.execute('SELECT s.id,r.status FROM searches s JOIN results r ON r.search_id=s.id WHERE s.id=? AND s.user_id=? AND r.username=?',(b.search_id,u['id'],b.username)).fetchone()
        if not row: raise HTTPException(404,'Result not found')
        if row['status']=='skipped': raise HTTPException(400,'Skipped results cannot be marked')
        c.execute('UPDATE results SET status=? WHERE search_id=? AND username=?',(b.status,b.search_id,b.username))
    return {'ok':True,'verification':'user-reported, not platform-confirmed'}
@app.get('/api/history')
def history(u=Depends(require)):
    with db() as c:
        searches=[dict(r) for r in c.execute('SELECT id,platform,created FROM searches WHERE user_id=? ORDER BY id DESC LIMIT 50',(u['id'],))]
        for s in searches: s['results']=[dict(r) for r in c.execute('SELECT username,status FROM results WHERE search_id=? LIMIT 200',(s['id'],))]
    return searches
@app.get('/api/favorites')
def favorites(u=Depends(require)):
    with db() as c: return [dict(r) for r in c.execute('SELECT platform,username FROM favorites WHERE user_id=? ORDER BY username',(u['id'],))]
@app.post('/api/favorites')
def add_favorite(b:Favorite,u=Depends(require)):
    with db() as c: c.execute('INSERT OR IGNORE INTO favorites VALUES(?,?,?)',(u['id'],b.platform,b.username))
    return {'ok':True}
@app.delete('/api/favorites/{platform}/{username}')
def del_favorite(platform:str,username:str,u=Depends(require)):
    with db() as c: c.execute('DELETE FROM favorites WHERE user_id=? AND platform=? AND username=?',(u['id'],platform,username))
    return {'ok':True}
@app.get('/api/presets')
def presets(u=Depends(require)):
    with db() as c: return [{'name':r['name'],'config':json.loads(r['config'])} for r in c.execute('SELECT name,config FROM presets WHERE user_id=?',(u['id'],))]
@app.post('/api/presets')
def save_preset(b:Preset,u=Depends(require)):
    if not 1<=len(b.name)<=50: raise HTTPException(400,'Invalid preset name')
    with db() as c: c.execute('INSERT INTO presets VALUES(?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET config=excluded.config',(u['id'],b.name,json.dumps(b.config)))
    return {'ok':True}
@app.get('/api/health')
def health(): return {'status':'ok'}
@app.get('/')
def index(): return FileResponse(ROOT/'frontend'/'index.html')
app.mount('/static',StaticFiles(directory=ROOT/'frontend'),name='static')
