"""Wischuy's district-scoped JSON API, backed by SQLite."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from getpass import getpass
import hashlib
from hmac import compare_digest
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
DISTRICTS = json.loads((BASE / 'districts.json').read_text())
LIMA = ZoneInfo('America/Lima')
ANNOUNCEMENTS = (
    'Aviso de recolección', 'Cambio de horario', 'Modificación de ruta',
    'Suspensión del servicio', 'Campaña de limpieza', 'Comunicado de emergencia',
)
INCIDENT_TYPES = ('Calle bloqueada', 'Vehículo averiado', 'Basura acumulada', 'Problema con residentes', 'Otro')
TOKEN_TTL = 8 * 60 * 60
MAX_BODY = 32_768


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def fail(message, status=400):
    raise ApiError(status, message)


def text(data, key, maximum, minimum=1):
    value = data.get(key)
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        fail(f'Revisa el campo {key}.')
    return value.strip()


def option(value, choices, label):
    if value not in choices:
        fail(f'Revisa {label}.')
    return value


def integer(value, label):
    if type(value) is not int or not 0 < value < 2**63:
        fail(f'Revisa {label}.')
    return value


def boolean(data, key):
    if type(data.get(key)) is not bool:
        fail(f'Revisa el campo {key}.')
    return data[key]


def selected(values, allowed, label):
    if not isinstance(values, list) or len(values) > len(allowed):
        fail(f'Revisa {label}.')
    result = []
    for value in values:
        option(value, allowed, label)
        if value not in result:
            result.append(value)
    return result


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=5)
    return f'{salt.hex()}:{digest.hex()}'


DUMMY_PASSWORD = password_hash(secrets.token_urlsafe(24))


def create_account(db, data, role, district):
    option(district, list(DISTRICTS), 'el distrito')
    username = text(data, 'usuario', 64)
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.@+-]{2,63}', username):
        fail('Usa un usuario de 3 a 64 caracteres sin espacios.')
    name = text(data, 'nombre', 100)
    password = data.get('contraseña')
    if not isinstance(password, str) or not 12 <= len(password) <= 128 or not password.strip():
        fail('La contraseña debe tener de 12 a 128 caracteres.')
    db.execute('INSERT INTO accounts(username,name,password,role,district) VALUES(?,?,?,?,?)',
               (username, name, password_hash(password), role, district))


def init_db(path):
    with connect(path) as db:
        db.executescript((BASE / 'schema.sql').read_text())


@contextmanager
def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA foreign_keys=ON')
        with db:
            yield db
    finally:
        db.close()


def authorize(db, request):
    match = re.fullmatch(r'Bearer ([A-Za-z0-9_-]{43})', request.get('HTTP_AUTHORIZATION', ''))
    if not match:
        fail('Inicia sesión para continuar.', 401)
    digest = hashlib.sha256(match[1].encode()).hexdigest()
    account = db.execute(
        'SELECT a.* FROM sessions s JOIN accounts a ON a.id=s.account_id '
        'WHERE s.digest=? AND s.expires>?', (digest, int(time.time()))
    ).fetchone()
    if not account:
        fail('La sesión terminó. Vuelve a iniciar sesión.', 401)
    return account, digest


def identity(account):
    return {key: account[key] for key in ('name', 'role', 'district')}


def login(db, data):
    role = option(data.get('rol'), ('municipality', 'driver', 'neighbor'), 'el perfil')
    username = text(data, 'usuario', 64)
    password = data.get('contraseña')
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        fail('Usuario, distrito o contraseña incorrectos.', 401)
    account = db.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
    stored = account['password'] if account else DUMMY_PASSWORD
    verified = compare_digest(password_hash(password, bytes.fromhex(stored.split(':')[0])), stored)
    if not (verified and account and account['role'] == role
            and (role != 'neighbor' or account['district'] == data.get('distrito'))):
        fail('Usuario, distrito o contraseña incorrectos.', 401)
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    db.execute('DELETE FROM sessions WHERE expires<=?', (now,))
    db.execute('INSERT INTO sessions VALUES(?,?,?)',
               (hashlib.sha256(token.encode()).hexdigest(), account['id'], now + TOKEN_TTL))
    return {'token': token, 'account': identity(account)}


TRUCKS = """
SELECT t.*, a.name AS driver,
  CASE WHEN EXISTS (SELECT 1 FROM runs r WHERE r.truck_id=t.id AND r.phase IN ('running','paused'))
       THEN 'on_route' ELSE t.condition END AS status
FROM trucks t LEFT JOIN accounts a ON a.id=t.driver_id
WHERE t.district=?
"""
ROUTES = """
SELECT r.*, t.code AS truck_code,t.name AS truck_name,t.plate AS truck_plate,
       t.sample AS truck_sample,t.active AS truck_active,t.condition AS truck_condition,
       a.name AS driver,
       CASE WHEN run.phase IN ('running','paused') THEN run.phase
            ELSE today.phase END AS phase,
       COALESCE(run.id, today.id) AS run_id
FROM routes r
JOIN trucks t ON t.id=r.truck_id AND t.district=r.district
JOIN accounts a ON a.id=r.driver_id AND a.district=r.district
LEFT JOIN runs run ON run.route_id=r.id AND run.phase IN ('running','paused')
LEFT JOIN runs today ON today.route_id=r.id AND today.service_date=?
WHERE r.district=? AND r.archived=0
"""


def local_date():
    return datetime.now(LIMA).date()


def truck_list(db, district):
    return [dict(row) for row in db.execute(TRUCKS + ' ORDER BY t.code', (district,))]


def route_list(db, district, condition='', args=()):
    values = db.execute(ROUTES + condition + ' ORDER BY r.name,r.id', (local_date().isoformat(), district, *args))
    result = []
    for row in values:
        route = dict(row)
        for field in ('streets', 'sectors', 'days', 'points'):
            route[field] = json.loads(route[field])
        route['attended'] = [r['street'] for r in db.execute(
            'SELECT street FROM attended WHERE run_id=? ORDER BY street', (route['run_id'],))]
        result.append(route)
    return result


def announcement_list(db, district):
    return [dict(row) for row in db.execute(
        'SELECT * FROM announcements WHERE district=? ORDER BY id DESC', (district,))]


def admin_data(db, account):
    district = account['district']
    trucks = truck_list(db, district)
    routes = route_list(db, district)
    announcements = announcement_list(db, district)
    streets_attended = db.execute(
        'SELECT count(DISTINCT s.street) FROM attended s JOIN runs r ON r.id=s.run_id '
        'WHERE r.district=? AND r.service_date=?', (district, local_date().isoformat())
    ).fetchone()[0]
    drivers = [dict(row) for row in db.execute(
        "SELECT id,name FROM accounts WHERE district=? AND role='driver' ORDER BY name", (district,))]
    return {
        'trucks': trucks, 'routes': routes, 'announcements': announcements, 'drivers': drivers,
        'stats': {
            'trucks': len(trucks), 'available': sum(t['active'] and t['status']=='available' for t in trucks),
            'on_route': sum(t['status']=='on_route' for t in trucks), 'routes': sum(r['active'] for r in routes),
            'streets': streets_attended, 'announcements': len(announcements),
        },
    }


def get_driver(db, district, value):
    driver_id = integer(value, 'el conductor')
    if not db.execute("SELECT 1 FROM accounts WHERE id=? AND district=? AND role='driver'",
                      (driver_id, district)).fetchone():
        fail('El conductor no está disponible en este distrito.', 422)
    return driver_id


def save_truck(db, district, data, truck_id=None):
    driver_id = data.get('conductor')
    if driver_id is not None:
        driver_id = get_driver(db, district, driver_id)
    plate = text(data, 'placa', 20, 0).upper() or None
    active = boolean(data, 'activo')
    condition = option(data.get('estado'), ('available', 'maintenance'), 'el estado')
    code = text(data, 'codigo', 30)
    name = text(data, 'nombre', 100)
    if truck_id is not None:
        existing = db.execute('SELECT * FROM trucks WHERE id=? AND district=?', (truck_id, district)).fetchone()
        if not existing:
            fail('Camión no encontrado.', 404)
        if (not active or condition != 'available' or driver_id != existing['driver_id']) and db.execute(
            "SELECT 1 FROM runs WHERE truck_id=? AND phase<>'finished'", (truck_id,)
        ).fetchone():
            fail('Finaliza el recorrido antes de cambiar la disponibilidad o el conductor.', 409)
    if plate is None and not (truck_id is not None and existing['sample']):
        fail('Ingresa la placa del vehículo.')
    values = (code, plate, name, driver_id, active, condition)
    if truck_id is None:
        truck_id = db.execute(
            'INSERT INTO trucks(code,plate,name,driver_id,active,condition,district) VALUES(?,?,?,?,?,?,?)',
            (*values,district)).lastrowid
    else:
        db.execute('UPDATE trucks SET code=?,plate=?,name=?,driver_id=?,active=?,condition=? WHERE id=? AND district=?',
                   (*values,truck_id,district))
    return {'id': truck_id}


def set_truck_active(db, district, truck_id, data):
    active = boolean(data, 'activo')
    if not db.execute('SELECT 1 FROM trucks WHERE id=? AND district=?', (truck_id,district)).fetchone():
        fail('Camión no encontrado.',404)
    if not active and db.execute("SELECT 1 FROM runs WHERE truck_id=? AND phase<>'finished'", (truck_id,)).fetchone():
        fail('Finaliza el recorrido antes de desactivar el camión.',409)
    db.execute('UPDATE trucks SET active=? WHERE id=? AND district=?',(active,truck_id,district))
    return {'id':truck_id}


def save_route(db, district, data, route_id=None):
    place = DISTRICTS[district]
    name = text(data, 'nombre', 110)
    streets = selected(data.get('calles'), place['streets'], 'las calles del distrito')
    sectors = selected(data.get('sectores'), place['sectors'], 'los sectores del distrito')
    days = selected(data.get('dias'), list(range(1,8)), 'los días de recolección')
    if not streets or not days or any(type(day) is not int for day in days):
        fail('Selecciona al menos una calle y un día de recolección.')
    start = text(data, 'desde', 5)
    end = text(data, 'hasta', 5)
    if any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t) for t in (start,end)) or start >= end:
        fail('Ingresa un horario válido del mismo día.')
    truck_id = integer(data.get('camion'), 'el camión')
    active = boolean(data, 'activa')
    truck = db.execute('SELECT * FROM trucks WHERE id=? AND district=?', (truck_id,district)).fetchone()
    if not truck or (active and not truck['active']):
        fail('El camión asignado debe pertenecer al distrito y estar habilitado para activar la ruta.', 422)
    driver_id = get_driver(db, district, data.get('conductor'))
    points = data.get('puntos')
    if not isinstance(points,list) or len(points)>100 or len(points)==1 or any(
        not isinstance(p,list) or len(p)!=2 or any(type(n) not in (float,int) or not 0<=n<=100 for n in p)
        for p in points
    ):
        fail('Usa dos o más puntos válidos en el mapa o deja el trazado vacío.')
    values = (name, json.dumps(streets), json.dumps(sectors), truck_id, driver_id, json.dumps(days),
              start,end,json.dumps(points),active)
    if route_id is None:
        route_id = db.execute(
            'INSERT INTO routes(name,streets,sectors,truck_id,driver_id,days,start_time,end_time,points,active,district) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (*values,district)).lastrowid
    else:
        check_route(db,district,route_id)
        db.execute(
            'UPDATE routes SET name=?,streets=?,sectors=?,truck_id=?,driver_id=?,days=?,start_time=?,end_time=?,points=?,active=? '
            'WHERE id=? AND district=?', (*values,route_id,district))
    return {'id': route_id}


def check_route(db, district, route_id):
    if not db.execute('SELECT 1 FROM routes WHERE id=? AND district=? AND archived=0', (route_id,district)).fetchone():
        fail('Ruta no encontrada.', 404)
    if db.execute("SELECT 1 FROM runs WHERE route_id=? AND phase<>'finished'", (route_id,)).fetchone():
        fail('Finaliza el recorrido antes de modificar o eliminar la ruta.', 409)


def public_route(route):
    fields = ('id','name','streets','sectors','days','start_time','end_time','points','phase','attended',
              'truck_code','truck_name','truck_plate','truck_sample','truck_active','truck_condition')
    return {key:route[key] for key in fields}


def update_run(db, account, route_id, data):
    district, driver = account['district'], account['id']
    matches = route_list(db,district,' AND r.id=? AND r.driver_id=?',(route_id,driver))
    if not matches:
        fail('Ruta no encontrada.', 404)
    route = matches[0]
    action = option(data.get('accion'), ('start','pause','resume','finish','attend'), 'la acción')
    date = local_date()
    if action == 'start':
        if (not route['active'] or route['phase'] or not route['truck_active']
                or route['truck_condition']!='available' or date.isoweekday() not in route['days']):
            fail('La ruta o su camión no están disponibles para el turno de hoy.', 409)
        db.execute(
            "INSERT INTO runs(district,route_id,truck_id,driver_id,service_date,phase) VALUES(?,?,?,?,?,'running')",
            (district,route_id,route['truck_id'],driver,date.isoformat()))
    else:
        if route['phase'] not in ('running','paused'):
            fail('La ruta no tiene un recorrido en curso.', 409)
        if action=='attend':
            street = option(data.get('calle'),route['streets'],'la calle')
            db.execute('INSERT OR IGNORE INTO attended(run_id,street) VALUES(?,?)', (route['run_id'],street))
        else:
            previous, phase = {'pause':('running','paused'),'resume':('paused','running'),
                               'finish':(route['phase'],'finished')}[action]
            if route['phase'] != previous:
                fail('El estado del recorrido cambió. Actualiza la vista.',409)
            db.execute('UPDATE runs SET phase=? WHERE id=?', (phase,route['run_id']))
    return {'ok': True}


def report_incident(db, account, route_id, data):
    district = account['district']
    route = route_list(db, district, " AND r.id=? AND r.driver_id=? AND (r.active=1 OR run.id IS NOT NULL)",
                       (route_id, account['id']))
    if not route:
        fail('Ruta no encontrada.', 404)
    category = option(data.get('categoria'), INCIDENT_TYPES, 'el tipo de incidencia')
    message = text(data, 'mensaje', 1000)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    result = db.execute(
        'INSERT INTO incidents(district,route_id,driver_id,category,message,reported_at) VALUES(?,?,?,?,?,?)',
        (district, route_id, account['id'], category, message, now))
    return {'id': result.lastrowid}


def district_incidents(db, district):
    reports = db.execute(
        'SELECT i.id,i.category,i.message,i.reported_at,r.name AS route,a.name AS driver '
        'FROM incidents i JOIN routes r ON r.id=i.route_id JOIN accounts a ON a.id=i.driver_id '
        'WHERE i.district=? ORDER BY i.id DESC LIMIT 100', (district,))
    return [dict(report) for report in reports]


def dispatch(db, request, method, path, data):
    if method=='GET' and path=='/districts':
        return DISTRICTS
    if method=='POST' and path=='/login':
        return login(db,data)
    if method=='POST' and path=='/neighbors':
        create_account(db,data,'neighbor',data.get('distrito'))
        return login(db,{**data,'rol':'neighbor'})
    account, digest = authorize(db,request)
    district, role = account['district'], account['role']
    if method=='POST' and path=='/logout':
        db.execute('DELETE FROM sessions WHERE digest=?',(digest,))
        return {'ok':True}
    if method=='GET' and path=='/me':
        return identity(account)
    query = parse_qs(request.get('QUERY_STRING',''),keep_blank_values=True)
    if any((key in data and data[key]!=district) or any(value!=district for value in query.get(key,()))
           for key in ('district','distrito')):
        fail('Esta cuenta pertenece a otro distrito.',403)

    if path.startswith('/admin/'):
        if role!='municipality':
            fail('Solo el perfil municipal puede administrar el distrito.',403)
        if method=='GET' and path=='/admin/incidents':
            return {'incidents': district_incidents(db,district)}
        if method=='GET' and path=='/admin/dashboard':
            return admin_data(db,account)
        if method=='POST' and path=='/admin/trucks':
            return save_truck(db,district,data)
        match = re.fullmatch(r'/admin/trucks/([1-9]\d{0,17})',path)
        if method=='PUT' and match:
            return save_truck(db,district,data,int(match[1]))
        if method=='PATCH' and match:
            return set_truck_active(db,district,int(match[1]),data)
        if method=='POST' and path=='/admin/routes':
            return save_route(db,district,data)
        match = re.fullmatch(r'/admin/routes/([1-9]\d{0,17})',path)
        if match and method=='PUT':
            return save_route(db,district,data,int(match[1]))
        if match and method=='DELETE':
            route_id=int(match[1])
            check_route(db,district,route_id)
            db.execute('UPDATE routes SET archived=1,active=0 WHERE id=? AND district=?',(route_id,district))
            return {'ok':True}
        announcement=re.fullmatch(r'/admin/announcements/([1-9]\d{0,17})',path)
        if announcement and method=='DELETE':
            deleted=db.execute('DELETE FROM announcements WHERE id=? AND district=?',
                               (int(announcement[1]),district))
            if not deleted.rowcount:
                fail('Comunicado no encontrado.',404)
            return {'ok': True}
        if method=='POST' and path=='/admin/announcements':
            category=option(data.get('categoria'),ANNOUNCEMENTS,'el tipo de comunicado')
            title=text(data,'titulo',120)
            message=text(data,'mensaje',2000)
            now=datetime.now(timezone.utc).isoformat(timespec='seconds')
            db.execute('INSERT INTO announcements(district,category,title,message,published_at) VALUES(?,?,?,?,?)',
                       (district,category,title,message,now))
            return {'ok':True}

    if method=='GET' and path=='/resident':
        if role!='neighbor':
            fail('Esta consulta corresponde al perfil vecino.',403)
        return {'routes':[public_route(r) for r in route_list(db,district,' AND r.active=1')],
                'announcements':announcement_list(db,district)}
    if path.startswith('/driver/'):
        if role!='driver':
            fail('Esta consulta corresponde al perfil conductor.',403)
        if method=='GET' and path=='/driver/routes':
            return {'routes':[public_route(r) for r in route_list(
                db,district," AND r.driver_id=? AND (r.active=1 OR run.id IS NOT NULL)",(account['id'],))],
                'announcements':announcement_list(db,district)}
        report=re.fullmatch(r'/driver/routes/([1-9]\d{0,17})/incidents',path)
        if report and method=='POST':
            return report_incident(db,account,int(report[1]),data)
        match=re.fullmatch(r'/driver/routes/([1-9]\d{0,17})/run',path)
        if match and method=='POST':
            return update_run(db,account,int(match[1]),data)
    fail('Operación no encontrada.',404)


def read_json(request):
    try:
        size=int(request.get('CONTENT_LENGTH') or 0)
        if not 0<=size<=MAX_BODY:
            fail('La solicitud excede el tamaño permitido.',413)
        result=json.loads(request['wsgi.input'].read(size)) if size else {}
    except (ValueError,UnicodeError):
        fail('El contenido de la solicitud no es válido.')
    if not isinstance(result,dict):
        fail('Envía un objeto JSON.')
    return result


def application(path, origins, web=False):
    static_files = {
        '/': ('index.html','text/html'), '/index.html': ('index.html','text/html'),
        '/municipal.js': ('municipal.js','text/javascript'),
        '/municipal.config.js': ('municipal.config.js','text/javascript'),
        '/municipal.css': ('municipal.css','text/css'),
    }

    def app(request, start_response):
        method=request['REQUEST_METHOD']
        endpoint=request['PATH_INFO']
        origin=request.get('HTTP_ORIGIN')
        if web and method=='GET' and endpoint in static_files:
            filename, mime = static_files[endpoint]
            encoded=(BASE.parent / filename).read_bytes()
            start_response('200 OK',[('Content-Type',mime+'; charset=utf-8'),
                ('Content-Length',str(len(encoded))),('X-Content-Type-Options','nosniff')])
            return [encoded]
        headers=[('Content-Type','application/json; charset=utf-8'),('Cache-Control','no-store'),
                 ('X-Content-Type-Options','nosniff'),('Vary','Origin')]
        if origin in origins:
            headers.extend([('Access-Control-Allow-Origin',origin),
                            ('Access-Control-Allow-Methods','GET, POST, PUT, PATCH, DELETE, OPTIONS'),
                            ('Access-Control-Allow-Headers','Content-Type, Authorization')])
        try:
            if origin and origin not in origins:
                fail('Origen no autorizado.',403)
            if not endpoint.startswith('/api/'):
                fail('Operación no encontrada.',404)
            if method=='OPTIONS':
                start_response('204 No Content',headers)
                return [b'']
            data=read_json(request)
            with connect(path) as db:
                db.execute('BEGIN IMMEDIATE' if method in ('POST','PUT','PATCH','DELETE') else 'BEGIN')
                result=dispatch(db,request,method,endpoint[4:],data)
            status='200 OK'
        except ApiError as exc:
            status=f'{exc.status} { {400:"Bad Request",401:"Unauthorized",403:"Forbidden",404:"Not Found",409:"Conflict",413:"Payload Too Large",422:"Unprocessable Entity"}[exc.status]}'
            result={'error':exc.message}
        except sqlite3.IntegrityError:
            status='409 Conflict'
            result={'error':'El registro está duplicado o el recurso ya está asignado a un recorrido en curso.'}
        encoded=json.dumps(result,ensure_ascii=False).encode()
        start_response(status,[*headers,('Content-Length',str(len(encoded)))])
        return [encoded]
    return app


def seed_demo(db):
    if any(db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in ('trucks','routes','accounts')):
        fail('Usa una base vacía para cargar el inventario de demostración.')
    for district in DISTRICTS:
        for number in range(1,4):
            db.execute(
                "INSERT INTO trucks(district,code,name,active,condition,sample) VALUES(?,?,?,1,'available',1)",
                (district,f'DEMO-{number:02}',f'Camión de demostración {number:02}'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True,help='Ruta persistente del archivo SQLite')
    sub=parser.add_subparsers(dest='command',required=True)
    serve=sub.add_parser('serve')
    serve.add_argument('--host',default='127.0.0.1')
    serve.add_argument('--port',type=int,default=8000)
    serve.add_argument('--origin',action='append',default=[])
    serve.add_argument('--web',action='store_true',help='Servir también la interfaz para desarrollo local')
    sub.add_parser('init-demo')
    account=sub.add_parser('add-account')
    account.add_argument('role',choices=('municipality','driver'))
    account.add_argument('district',choices=DISTRICTS)
    account.add_argument('username')
    account.add_argument('name')
    args=parser.parse_args()
    init_db(args.db)
    if args.command=='serve':
        print(f'API Wischuy en http://{args.host}:{args.port}/api',flush=True)
        origins=set(args.origin)
        if args.web:
            origins.update(f'http://{host}:{args.port}' for host in ('localhost','127.0.0.1'))
        with make_server(args.host,args.port,application(args.db,origins,args.web)) as server:
            server.serve_forever()
        return
    try:
        if args.command=='add-account':
            password=getpass('Contraseña: ')
            if password!=getpass('Confirma la contraseña: '):
                fail('Las contraseñas no coinciden.')
        with connect(args.db) as db:
            db.execute('BEGIN IMMEDIATE')
            if args.command=='init-demo':
                seed_demo(db)
            else:
                create_account(db,{'usuario':args.username,'nombre':args.name,'contraseña':password},
                               args.role,args.district)
    except (ApiError,sqlite3.IntegrityError) as exc:
        parser.exit(1,f'{getattr(exc,"message","El usuario ya existe.")}\n')


if __name__=='__main__':
    main()
