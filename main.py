import os, json, sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote_plus

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jose import jwt, JWTError
from passlib.context import CryptContext

load_dotenv()
ROOT = Path(__file__).resolve().parent
DB_PATH = os.getenv('DATABASE_PATH', 'pocketsmart.db')
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-change-me')
COOKIE = 'pocketsmart_session'
MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
pwd = CryptContext(schemes=['bcrypt'], deprecated='auto')
app = FastAPI(title='PocketSmart AI', version='1.0.0')
app.mount('/static', StaticFiles(directory=str(ROOT / 'static')), name='static')
templates = Jinja2Templates(directory=str(ROOT / 'templates'))


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    db = connect()
    db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
    db.execute('CREATE TABLE IF NOT EXISTS recommendations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, planner_type TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
    db.commit(); db.close()

@app.on_event('startup')
def startup(): init_db()


def make_token(user_id, username):
    now = datetime.now(timezone.utc)
    return jwt.encode({'sub': str(user_id), 'username': username, 'iat': now, 'exp': now + timedelta(days=7)}, SECRET_KEY, algorithm='HS256')


def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        auth = request.headers.get('authorization', '')
        if auth.lower().startswith('bearer '): token = auth[7:].strip()
    if not token: return None
    try: payload = jwt.decode(token, SECRET_KEY, algorithms=['HS256']); uid = int(payload['sub'])
    except (JWTError, ValueError, KeyError): return None
    db = connect(); row = db.execute('SELECT id, username, email, created_at FROM users WHERE id=?', (uid,)).fetchone(); db.close()
    return dict(row) if row else None


def require_user(request):
    user = current_user(request)
    if not user: raise HTTPException(401, 'Please log in first.')
    return user


def render(request, template, **extra):
    return templates.TemplateResponse(request=request, name=template, context={'user': current_user(request), **extra})


def demo_result(planner, data):
    budget = float(data['budget'])
    if planner == 'home':
        rows = [('Lighting', .15, 'LED lighting and bulbs', 'Amazon'), ('Furniture', .35, 'Space-saving furniture', 'IKEA / Amazon'), ('Storage', .20, 'Storage and organization', 'Amazon / Flipkart'), ('Decor', .15, 'Curtains, rugs and wall art', 'Flipkart'), ('Reserve', .15, 'Delivery and unexpected costs', 'Budget tip')]
        title = 'Home interior plan'
    elif planner == 'party':
        rows = [('Food & drinks', .45, 'Compare catering and meal packages', 'Swiggy / Zomato'), ('Decor', .20, 'Backdrop, balloons and table decor', 'Amazon / Flipkart'), ('Venue', .20, 'Small hall or suitable local venue', 'Local venue search'), ('Entertainment', .10, 'Music, games and activities', 'DIY'), ('Reserve', .05, 'Contingency reserve', 'Budget tip')]
        title = 'Party budget plan'
    else:
        rows = [('Earrings', .25, 'Matching lightweight earrings', 'Amazon / Flipkart'), ('Necklace', .35, 'Necklace matching the outfit and occasion', 'Amazon / Flipkart'), ('Bangles / bracelet', .20, 'Coordinated wrist accessories', 'Amazon / Flipkart'), ('Hair accessory', .10, 'Subtle hair accessory', 'Amazon'), ('Reserve', .10, 'Reserve for adjustments', 'Budget tip')]
        title = 'Jewelry recommendation plan'
    items = []
    for category, fraction, idea, platform in rows:
        q = quote_plus(idea + ' ' + str(data.get('style', data.get('event_type', ''))))
        items.append({'category': category, 'idea': idea, 'estimated_budget': round(budget*fraction, 2), 'platform': platform, 'search_url': 'https://www.google.com/search?q='+q})
    return {'title': title, 'summary': 'Planning estimates based on your inputs. Check current prices and availability before purchasing.', 'total_budget': budget, 'currency': data.get('currency', 'INR'), 'items': items, 'tips': ['Compare prices, reviews, delivery fees and return policies.', 'Marketplace prices and availability are not live-verified by this demo.', 'Keep the reserve for unexpected costs.'], 'source': 'demo'}


def generate_ai(planner, data, image_bytes=None, image_mime=None):
    fallback = demo_result(planner, data)
    if not API_KEY: return fallback
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=API_KEY)
        prompt = f'''You are PocketSmart AI, a practical budget recommendation assistant. Planner: {planner}. User details: {json.dumps(data)}. Never exceed the budget. Do not claim live inventory, verified current prices, or direct marketplace access. Return ONLY JSON with keys title, summary, total_budget, currency, items (array of objects with category, idea, estimated_budget, platform, search_url), tips (array of strings). Use 4-7 items and estimates. Currency is INR. For jewelry, describe only broad outfit colors/style from the image; do not identify people.'''
        content = [prompt]
        if image_bytes and image_mime: content.append(types.Part.from_bytes(data=image_bytes, mime_type=image_mime))
        response = client.models.generate_content(model=MODEL, contents=content)
        raw = (response.text or '').strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get('items'), list): return fallback
        result.setdefault('title', fallback['title']); result.setdefault('summary', fallback['summary']); result.setdefault('total_budget', float(data['budget'])); result.setdefault('currency', 'INR'); result.setdefault('tips', fallback['tips']); result['source'] = 'gemini'
        total = sum(max(0, float(x.get('estimated_budget', 0) or 0)) for x in result['items']); budget = float(data['budget'])
        if total > budget and total > 0:
            for x in result['items']: x['estimated_budget'] = round(max(0, float(x.get('estimated_budget', 0) or 0))*budget/total, 2)
        for x in result['items']:
            x.setdefault('category', 'Suggestion'); x.setdefault('idea', 'Review this option'); x.setdefault('platform', 'Search online')
            x.setdefault('search_url', 'https://www.google.com/search?q='+quote_plus(str(x['idea'])))
        return result
    except Exception:
        return fallback


def save_result(user_id, planner, data, result):
    db = connect(); cur = db.execute('INSERT INTO recommendations (user_id, planner_type, request_json, result_json) VALUES (?,?,?,?)', (user_id, planner, json.dumps(data), json.dumps(result))); db.commit(); rid = cur.lastrowid; db.close(); result['recommendation_id'] = rid

@app.get('/health')
def health(): return {'status':'ok', 'app':'PocketSmart AI'}
@app.get('/startup')
def startup_route(): init_db(); return {'status':'initialized'}
@app.get('/', response_class=HTMLResponse)
def home(request: Request): return render(request, 'index.html')
@app.get('/register', response_class=HTMLResponse)
def register_page(request: Request): return render(request, 'register.html', error=None)
@app.post('/register')
def register(request: Request, username: str=Form(...), email: str=Form(...), password: str=Form(...)):
    username, email = username.strip(), email.strip().lower()
    if len(username)<2 or len(password)<8: return render(request, 'register.html', error='Username needs 2+ characters and password needs 8+ characters.')
    db=connect()
    try:
        cur=db.execute('INSERT INTO users (username,email,password_hash) VALUES (?,?,?)',(username,email,pwd.hash(password))); db.commit(); uid=cur.lastrowid
    except sqlite3.IntegrityError:
        db.close(); return render(request, 'register.html', error='That username or email is already registered.')
    db.close(); response=RedirectResponse('/dashboard',status_code=303); response.set_cookie(COOKIE,make_token(uid,username),httponly=True,samesite='lax',secure=False,max_age=604800); return response
@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request): return render(request,'login.html',error=None)
@app.post('/login')
def login(request: Request, username: str=Form(...), password: str=Form(...)):
    db=connect(); row=db.execute('SELECT * FROM users WHERE username=? OR email=?',(username.strip(),username.strip().lower())).fetchone(); db.close()
    if not row or not pwd.verify(password,row['password_hash']): return render(request,'login.html',error='Incorrect username/email or password.')
    response=RedirectResponse('/dashboard',status_code=303); response.set_cookie(COOKIE,make_token(row['id'],row['username']),httponly=True,samesite='lax',secure=False,max_age=604800); return response
@app.post('/logout')
def logout():
    response=RedirectResponse('/',status_code=303); response.delete_cookie(COOKIE); return response
@app.get('/dashboard', response_class=HTMLResponse)
def dashboard(request: Request):
    user=current_user(request)
    if not user: return RedirectResponse('/login',status_code=303)
    db=connect(); rows=db.execute('SELECT id,planner_type,created_at FROM recommendations WHERE user_id=? ORDER BY id DESC LIMIT 5',(user['id'],)).fetchall(); db.close()
    return render(request,'dashboard.html',recent=[dict(r) for r in rows])
@app.get('/planner/{planner}', response_class=HTMLResponse)
def planner_page(request: Request, planner: str):
    if planner not in ('home','party','jewelry'): raise HTTPException(404,'Planner not found')
    return render(request,'planner.html',planner=planner)

@app.post('/generate-home', response_class=HTMLResponse)
def generate_home(request: Request, budget: float=Form(...), rooms: str=Form('Living room'), style: str=Form('Modern'), items: str=Form('Lighting, storage, decor'), currency: str=Form('INR')):
    user=require_user(request)
    if budget<=0: raise HTTPException(422,'Budget must be greater than zero.')
    data={'budget':budget,'rooms':rooms,'style':style,'items':items,'currency':currency}; result=generate_ai('home',data); save_result(user['id'],'home',data,result)
    return render(request,'results.html',planner='home',result=result)
@app.post('/generate-party', response_class=HTMLResponse)
def generate_party(request: Request, budget: float=Form(...), guests: int=Form(...), event_type: str=Form('Birthday'), venue: str=Form('Not decided'), preferences: str=Form(''), currency: str=Form('INR')):
    user=require_user(request)
    if budget<=0 or guests<1: raise HTTPException(422,'Enter a positive budget and guest count.')
    data={'budget':budget,'guests':guests,'event_type':event_type,'venue':venue,'preferences':preferences,'currency':currency}; result=generate_ai('party',data); save_result(user['id'],'party',data,result)
    return render(request,'results.html',planner='party',result=result)
@app.post('/generate-jewelry', response_class=HTMLResponse)
async def generate_jewelry(request: Request, budget: float=Form(...), occasion: str=Form('Everyday'), style: str=Form('Minimal'), outfit_color: str=Form('Not specified'), currency: str=Form('INR'), outfit_image: Optional[UploadFile]=File(None)):
    user=require_user(request)
    if budget<=0: raise HTTPException(422,'Budget must be greater than zero.')
    image_bytes=image_mime=None
    if outfit_image and outfit_image.filename:
        image_bytes=await outfit_image.read(); image_mime=outfit_image.content_type or ''
        if len(image_bytes)>5*1024*1024: raise HTTPException(413,'Image must be 5 MB or smaller.')
        if image_mime not in ('image/jpeg','image/png','image/webp'): raise HTTPException(415,'Upload JPG, PNG, or WEBP image.')
    data={'budget':budget,'occasion':occasion,'style':style,'outfit_color':outfit_color,'currency':currency}; result=generate_ai('jewelry',data,image_bytes,image_mime); save_result(user['id'],'jewelry',data,result)
    return render(request,'results.html',planner='jewelry',result=result)

@app.post('/token')
def token(username: str=Form(...), password: str=Form(...)):
    db=connect(); row=db.execute('SELECT * FROM users WHERE username=? OR email=?',(username.strip(),username.strip().lower())).fetchone(); db.close()
    if not row or not pwd.verify(password,row['password_hash']): raise HTTPException(401,'Invalid credentials')
    return {'access_token':make_token(row['id'],row['username']),'token_type':'bearer'}
@app.get('/session-info')
def session_info(request: Request):
    user=current_user(request); return {'authenticated':bool(user),'user':user}
@app.get('/session-data')
def session_data(request: Request):
    user=require_user(request); db=connect(); count=db.execute('SELECT COUNT(*) n FROM recommendations WHERE user_id=?',(user['id'],)).fetchone()['n']; db.close(); return {'user':user,'recommendation_count':count}
@app.get('/history', response_class=HTMLResponse)
def history(request: Request):
    user=current_user(request)
    if not user: return RedirectResponse('/login',status_code=303)
    db=connect(); rows=db.execute('SELECT * FROM recommendations WHERE user_id=? ORDER BY id DESC',(user['id'],)).fetchall(); db.close(); entries=[]
    for r in rows:
        x=dict(r); x['request']=json.loads(x.pop('request_json')); x['result']=json.loads(x.pop('result_json')); entries.append(x)
    return render(request,'history.html',entries=entries)
@app.get('/recommendations-details/{recommendation_id}')
def recommendation_details(request: Request, recommendation_id: int):
    user=require_user(request); db=connect(); r=db.execute('SELECT * FROM recommendations WHERE id=? AND user_id=?',(recommendation_id,user['id'])).fetchone(); db.close()
    if not r: raise HTTPException(404,'Recommendation not found')
    return {'id':r['id'],'planner_type':r['planner_type'],'request':json.loads(r['request_json']),'result':json.loads(r['result_json']),'created_at':r['created_at']}
@app.get('/testimonials', response_class=HTMLResponse)
def testimonials(request: Request): return render(request,'testimonials.html')
