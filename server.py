#!/usr/bin/env python3
"""THE SI BEAUTY LOUNGE — standalone web app (stdlib only)."""
import base64, copy, hashlib, hmac, json, mimetypes, os, re, secrets, sqlite3, threading, time, uuid
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT=Path(__file__).resolve().parent
WEB=ROOT/'public'
DATADIR=Path(os.environ.get('DATA_DIR',str(ROOT/'data')))
DATADIR.mkdir(parents=True,exist_ok=True)
UPLOADS=DATADIR/'uploads'; UPLOADS.mkdir(exist_ok=True)
DB=DATADIR/'site.db'
HOST=os.environ.get('HOST','0.0.0.0')
PORT=int(os.environ.get('PORT','8000'))
SECURE=os.environ.get('SECURE_COOKIES','0')=='1'
MAX_BODY=15*1024*1024
lock=threading.RLock()

CAT=[
('coiffure','Coiffure & Soins Capillaires','Coupes, colorations, soins experts et rituels naturels',4,12),
('head-spa','Head Spa','Nos rituels coréens et japonais',13,19),
('onglerie','Onglerie','Manucures, pédicures et protocoles VIP',20,26),
('regard','Cils & Sourcils','L’art de sublimer votre regard',27,28),
('mariee','Mariée & Invitée','Vos instants les plus précieux',29,29),
('hammam','Hammam & Rituels','L’héritage du bien-être marocain',30,41),
('massages','Massages','L’art du toucher et de la relaxation',42,42),
('visage','Soins du Visage','L’expertise au service de votre peau',43,44),
('epilation','Épilation & Laser','Douceur et précision',45,46),
('corps','Soins du Corps','Technologies et rituels ciblés',47,48),
('packs','Packs & Expériences','Prolongez vos moments de beauté',49,49),
('anniversaire','Anniversaires','Célébrer en beauté',50,51),
]

def initial_data():
 return {'settings':{'brand':'THE SI BEAUTY LOUNGE','tagline':'Where Beauty Meets Luxury','heading':'L’art de prendre soin de soi','subheading':'Découvrez nos univers de beauté et de bien-être. Une expérience d’exception, pensée dans les moindres détails.','phone':'+212 624 898 505','whatsapp':'212624898505','website':'thesibeautylounge.com','instagram':'','location':'Rabat · Agdal, Maroc','hours':'Tous les jours · 10h–20h','logo':'','hero':'/assets/banners/accueil.webp'},'categories':[{'id':k,'name':name,'description':desc,'image':'/assets/banners/'+k+'.webp','pageIds':list(range(lo,hi+1)),'visible':True} for k,name,desc,lo,hi in CAT], 'pages':[{'id':i,'image':f'/assets/pages/page-{i:02}.webp','label':f'Page {i}'} for i in range(1,53)]}

def conn():
 c=sqlite3.connect(DB,timeout=30)
 c.row_factory=sqlite3.Row
 return c

def init_db():
 with conn() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, salt TEXT NOT NULL, password TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires INTEGER NOT NULL);
  CREATE TABLE IF NOT EXISTS revisions (id INTEGER PRIMARY KEY AUTOINCREMENT, created INTEGER, data TEXT);''')
  if not c.execute("SELECT 1 FROM state WHERE key='published'").fetchone():
   d=json.dumps(initial_data(),ensure_ascii=False)
   c.execute('INSERT INTO state VALUES (?,?)',('published',d))
   c.execute('INSERT INTO state VALUES (?,?)',('draft',d))
  email=os.environ.get('ADMIN_EMAIL','').strip().lower()
  password=os.environ.get('ADMIN_PASSWORD','')
  if email and password and not c.execute('SELECT 1 FROM admins').fetchone():
   if len(password)<12: raise RuntimeError('ADMIN_PASSWORD doit avoir au moins 12 caractères')
   salt=secrets.token_hex(16); ph=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),450000).hex()
   c.execute('INSERT INTO admins (email,salt,password) VALUES (?,?,?)',(email,salt,ph))

def load(key):
 with conn() as c:
  return json.loads(c.execute('SELECT value FROM state WHERE key=?',(key,)).fetchone()['value'])

def save(key,data):
 with lock, conn() as c:
  c.execute('UPDATE state SET value=? WHERE key=?',(json.dumps(data,ensure_ascii=False),key))

def user_token(h):
 ck=SimpleCookie()
 try: ck.load(h.headers.get('Cookie',''))
 except Exception: return None
 raw=ck.get('si_session')
 if not raw: return None
 digest=hashlib.sha256(raw.value.encode()).hexdigest()
 with conn() as c:
  if c.execute('SELECT 1 FROM sessions WHERE token_hash=? AND expires>?',(digest,int(time.time()))).fetchone(): return digest
 return None

def valid_img(path):
 return isinstance(path,str) and (path.startswith('/assets/') or (path.startswith('/media/') and '..' not in path))

def clean_state(d):
 if not isinstance(d,dict): raise ValueError('Données incorrectes')
 a=initial_data(); settings=d.get('settings',{})
 if not isinstance(settings,dict): raise ValueError('Informations invalides')
 s={key:str(settings.get(key,default))[:1200] for key,default in a['settings'].items()}
 for k in ('hero','logo'):
  if s[k] and not valid_img(s[k]): raise ValueError('Image non autorisée')
 cats=d.get('categories',[]); pages=d.get('pages',[])
 if not isinstance(cats,list) or not isinstance(pages,list) or len(cats)>100 or len(pages)>300: raise ValueError('Trop de données')
 normalized_pages=[]; ids=set()
 for p in pages:
  pid=int(p['id']); img=p['image']
  if pid in ids or pid<1 or not valid_img(img): raise ValueError('Pages invalides')
  ids.add(pid); normalized_pages.append({'id':pid,'image':img,'label':str(p.get('label',f'Page {pid}'))[:120]})
 normalized_cats=[]; cat_ids=set()
 for cat in cats:
  cid=str(cat['id'])[:60]
  if not re.fullmatch(r'[a-z0-9-]+',cid) or cid in cat_ids: raise ValueError('Catégorie invalide')
  cat_ids.add(cid); img=cat['image']
  if not valid_img(img): raise ValueError('Image invalide')
  pg=[int(i) for i in cat.get('pageIds',[]) if int(i) in ids]
  normalized_cats.append({'id':cid,'name':str(cat['name'])[:100], 'description':str(cat.get('description',''))[:300], 'image':img,'pageIds':pg,'visible':bool(cat.get('visible',True))})
 return {'settings':s,'categories':normalized_cats,'pages':normalized_pages}

class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(WEB),**kwargs)
 def log_message(self,fmt,*args): print('%s %s'%(self.address_string(),fmt%args),flush=True)
 def send_json(self,code,data,extra=None):
  raw=json.dumps(data,ensure_ascii=False).encode()
  self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
  if extra:
   for k,v in extra.items(): self.send_header(k,v)
  self.end_headers();self.wfile.write(raw)
 def read_body(self):
  n=int(self.headers.get('Content-Length','0'))
  if n<0 or n>MAX_BODY: raise ValueError('Fichier trop volumineux (limite 15 Mo)')
  return self.rfile.read(n)
 def check_origin(self):
  origin=self.headers.get('Origin',''); host=self.headers.get('Host','')
  return not origin or urlparse(origin).netloc==host
 def protected(self):
  if not user_token(self): self.send_json(401,{'error':'Connexion nécessaire'});return False
  if not self.check_origin(): self.send_json(403,{'error':'Origine non autorisée'});return False
  return True
 def do_GET(self):
  url=urlparse(self.path); p=url.path
  if p=='/api/site': self.send_json(200,load('published'));return
  if p=='/api/admin/status':
   with conn() as c: ready=c.execute('SELECT 1 FROM admins').fetchone() is not None
   self.send_json(200,{'loggedIn':bool(user_token(self)),'configured':ready});return
  if p=='/api/admin/draft':
   if self.protected(): self.send_json(200,load('draft'))
   return
  if p.startswith('/media/'):
   name=p.split('/')[-1]
   if not re.fullmatch(r'[a-f0-9]{32}\.(webp|jpg|jpeg|png|pdf)',name): self.send_error(404);return
   file=UPLOADS/name
   if not file.is_file(): self.send_error(404);return
   self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(name)[0] or 'application/octet-stream');self.send_header('Content-Length',str(file.stat().st_size));self.send_header('Cache-Control','public, max-age=31536000, immutable');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
   with file.open('rb') as f:
    while b:=f.read(128*1024):self.wfile.write(b)
   return
  if p=='/admin' or p=='/admin/': self.path='/admin.html'
  elif p.startswith('/categorie/'): self.path='/index.html'
  elif p=='/':self.path='/index.html'
  return super().do_GET()
 def do_POST(self):
  p=urlparse(self.path).path
  if not self.check_origin():self.send_json(403,{'error':'Requête refusée'});return
  try:
   if p=='/api/admin/login':
    d=json.loads(self.read_body());email=str(d.get('email','')).strip().lower();password=str(d.get('password',''))
    with conn() as c: row=c.execute('SELECT * FROM admins WHERE email=?',(email,)).fetchone()
    if not row: self.send_json(401,{'error':'Identifiants incorrects'});return
    pw=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(row['salt']),450000).hex()
    if not hmac.compare_digest(pw,row['password']):self.send_json(401,{'error':'Identifiants incorrects'});return
    token=secrets.token_urlsafe(40); digest=hashlib.sha256(token.encode()).hexdigest()
    with conn() as c: c.execute('INSERT INTO sessions VALUES (?,?)',(digest,int(time.time())+60*60*24*7))
    cookie=f'si_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800'+('; Secure' if SECURE else '')
    self.send_json(200,{'ok':True},{'Set-Cookie':cookie});return
   if not self.protected():return
   if p=='/api/admin/logout':
    token=user_token(self)
    with conn() as c: c.execute('DELETE FROM sessions WHERE token_hash=?',(token,))
    self.send_json(200,{'ok':True},{'Set-Cookie':'si_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'});return
   if p=='/api/admin/save':
    d=clean_state(json.loads(self.read_body()))
    save('draft',d);self.send_json(200,{'ok':True});return
   if p=='/api/admin/publish':
    with lock,conn() as c:
     published=c.execute("SELECT value FROM state WHERE key='published'").fetchone()['value']
     draft=c.execute("SELECT value FROM state WHERE key='draft'").fetchone()['value']
     c.execute('INSERT INTO revisions (created,data) VALUES (?,?)',(int(time.time()),published))
     c.execute('UPDATE state SET value=? WHERE key=?',(draft,'published'))
    self.send_json(200,{'ok':True});return
   if p=='/api/admin/upload':
    content_type=self.headers.get('Content-Type','').split(';')[0].lower()
    ext={'image/jpeg':'jpg','image/png':'png','image/webp':'webp','application/pdf':'pdf'}.get(content_type)
    if not ext:self.send_json(400,{'error':'Format non accepté (JPG, PNG, WebP ou PDF)'});return
    body=self.read_body()
    if len(body)<20:self.send_json(400,{'error':'Fichier vide'});return
    if ext=='jpg' and not body.startswith(b'\xff\xd8'):raise ValueError('JPEG invalide')
    if ext=='png' and not body.startswith(b'\x89PNG'):raise ValueError('PNG invalide')
    if ext=='webp' and not (body.startswith(b'RIFF') and body[8:12]==b'WEBP'):raise ValueError('WebP invalide')
    if ext=='pdf' and not body.startswith(b'%PDF-'):raise ValueError('PDF invalide')
    name=uuid.uuid4().hex+'.'+ext
    (UPLOADS/name).write_bytes(body)
    self.send_json(200,{'url':'/media/'+name});return
   self.send_json(404,{'error':'Action inconnue'})
  except (ValueError,KeyError,TypeError,json.JSONDecodeError) as ex:self.send_json(400,{'error':str(ex)[:200]})
  except Exception as ex: print('SERVER ERROR',repr(ex),flush=True);self.send_json(500,{'error':'Erreur serveur'})

if __name__=='__main__':
 init_db()
 with conn() as c: configured=bool(c.execute('SELECT 1 FROM admins').fetchone())
 print(f'THE SI — http://localhost:{PORT} | Administration: http://localhost:{PORT}/admin | compte administrateur configuré: {configured}',flush=True)
 if not configured: print('ATTENTION: définir ADMIN_EMAIL et ADMIN_PASSWORD puis redémarrer pour activer l’administration.',flush=True)
 ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
