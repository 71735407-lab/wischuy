"""Exercise persistence and permissions through the HTTP API."""
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest
from wsgiref.simple_server import WSGIRequestHandler, make_server

from backend import server


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class MunicipalFlow(unittest.TestCase):
    password = 'contraseña temporal de pruebas'

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'test.sqlite'
        server.init_db(self.db)
        with server.connect(self.db) as db:
            server.seed_demo(db)
            for username, district, role in [
                ('muni-cusco','cusco','municipality'),
                ('muni-saylla','saylla','municipality'),
                ('driver-cusco','cusco','driver'),
                ('driver-other','cusco','driver'),
                ('driver-saylla','saylla','driver'),
            ]:
                server.create_account(db,{'usuario':username,'nombre':username,'contraseña':self.password},role,district)
        self.start_api()
        self.addCleanup(self.stop_api)

    def start_api(self):
        self.api = make_server('127.0.0.1',0,server.application(self.db,{'https://71735407-lab.github.io'},True),
                               handler_class=QuietHandler)
        self.thread = Thread(target=self.api.serve_forever,daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.api.server_port}'

    def stop_api(self):
        self.api.shutdown()
        self.api.server_close()
        self.thread.join()

    def call(self,path,*,method='GET',token=None,data=None,expected=200):
        headers = {'Authorization':f'Bearer {token}'} if token else {}
        if data is not None:
            headers['Content-Type']='application/json'
        req=Request(self.base+'/api'+path,headers=headers,method=method,
                    data=json.dumps(data).encode() if data is not None else None)
        try:
            response=urlopen(req,timeout=4)
        except HTTPError as exc:
            response=exc
        with response:
            result=json.load(response)
            self.assertEqual(response.status,expected,result)
            return result

    def login(self,role,username,district=None,expected=200):
        return self.call('/login',method='POST',data={'rol':role,'usuario':username,
                         'contraseña':self.password,'distrito':district},expected=expected).get('token')

    def register(self,username,district):
        return self.call('/neighbors',method='POST',data={'usuario':username,'nombre':username,
                         'contraseña':self.password,'distrito':district,'rol':'municipality'})['token']

    def test_scoped_catalog_and_inventory(self):
        catalog=self.call('/districts')
        self.assertEqual(len(catalog),8)
        self.assertIn('Avenida El Sol',catalog['cusco']['streets'])
        self.assertNotIn('Avenida El Sol',catalog['saylla']['streets'])
        with server.connect(self.db) as db:
            counts=db.execute('SELECT district,count(*),sum(sample) FROM trucks GROUP BY district').fetchall()
            self.assertEqual({(row[0],row[1],row[2]) for row in counts},{(d,3,3) for d in catalog})
        cusco=self.login('municipality','muni-cusco')
        saylla=self.login('municipality','muni-saylla')
        one=self.call('/admin/dashboard',token=cusco)
        two=self.call('/admin/dashboard',token=saylla)
        self.assertEqual(one['stats']['available'],3)
        self.assertTrue(all(t['district']=='cusco' and t['plate'] is None for t in one['trucks']))
        self.call('/admin/dashboard?district=saylla',token=cusco,expected=403)
        self.call('/admin/dashboard',expected=401)
        truck=two['trucks'][0]['id']
        self.call(f'/admin/trucks/{truck}',method='PATCH',token=cusco,data={'activo':False},expected=404)
        self.call(f'/admin/trucks/{truck}',method='PATCH',token=saylla,data={'activo':False})
        self.assertEqual(self.call('/admin/dashboard',token=saylla)['stats']['available'],2)
        self.assertEqual(self.call('/admin/dashboard',token=cusco)['stats']['available'],3)
        self.call(f'/admin/trucks/{truck}',method='PATCH',token=saylla,data={'activo':True})

    def test_cors_for_pages(self):
        def preflight(origin):
            return Request(self.base+'/api/admin/trucks/1',method='OPTIONS',headers={
                'Origin':origin, 'Access-Control-Request-Method':'PATCH',
                'Access-Control-Request-Headers':'authorization,content-type',
            })
        origin='https://71735407-lab.github.io'
        with urlopen(preflight(origin),timeout=4) as response:
            self.assertEqual(response.status,204)
            self.assertEqual(response.headers['Access-Control-Allow-Origin'],origin)
            self.assertIn('PATCH',response.headers['Access-Control-Allow-Methods'])
        with self.assertRaises(HTTPError) as failure:
            urlopen(preflight('https://otro-dominio.example'),timeout=4)
        with failure.exception as response:
            self.assertEqual(response.code,403)
            self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))

    def test_route_communication_and_restart(self):
        muni=self.login('municipality','muni-cusco')
        foreign=self.login('municipality','muni-saylla')
        driver=self.login('driver','driver-cusco')
        other=self.login('driver','driver-other')
        vecino=self.register('vecino-cusco','cusco')
        vecino_foreign=self.register('vecino-saylla','saylla')
        self.login('neighbor','vecino-cusco','saylla',expected=401)
        self.call('/resident?distrito=saylla',token=vecino,expected=403)
        self.call('/admin/dashboard',token=vecino,expected=403)
        self.call('/admin/dashboard',token=driver,expected=403)
        self.assertEqual(self.call('/me',token=vecino)['role'],'neighbor')
        district=self.call('/admin/dashboard',token=muni)
        driver_id=next(d['id'] for d in district['drivers'] if d['name']=='driver-cusco')
        foreign_data=self.call('/admin/dashboard',token=foreign)
        truck_payload={'codigo':'PRUEBA-4','nombre':'Registro de prueba','placa':'TEST-4',
                       'conductor':driver_id,'activo':True,'estado':'available'}
        truck_id=self.call('/admin/trucks',method='POST',token=muni,data=truck_payload)['id']
        self.call(f'/admin/trucks/{truck_id}',method='PUT',token=muni,data={**truck_payload,'nombre':'Vehículo de prueba'})
        self.call('/admin/trucks',method='POST',token=muni,data={
            **truck_payload,'conductor':foreign_data['drivers'][0]['id']},expected=422)
        route={'nombre':'Ruta de prueba','calles':['Avenida El Sol'],'sectores':['Centro histórico'],
               'dias':[datetime.now(server.LIMA).isoweekday()],'desde':'07:00','hasta':'09:00','camion':truck_id,
               'conductor':driver_id,'puntos':[[20,30],[60,70]],'activa':True}
        self.call('/admin/routes',method='POST',token=muni,data={**route,'calles':['Calle Independencia']},expected=400)
        self.call('/admin/routes',method='POST',token=muni,data={
            **route,'camion':foreign_data['trucks'][0]['id']},expected=422)
        route_id=self.call('/admin/routes',method='POST',token=muni,data=route)['id']
        endpoint=f'/admin/routes/{route_id}'
        run=f'/driver/routes/{route_id}/run'
        self.call(endpoint,method='DELETE',token=foreign,expected=404)
        self.call(run,method='POST',token=other,data={'accion':'start'},expected=404)
        self.assertEqual(len(self.call('/driver/routes',token=other)['routes']),0)
        self.call(run,method='POST',token=driver,data={'accion':'start'})
        self.call(run,method='POST',token=driver,data={'accion':'attend','calle':'Avenida El Sol'})
        self.call(run,method='POST',token=driver,data={'accion':'attend','calle':'Avenida El Sol'})
        self.call(f'/admin/trucks/{truck_id}',method='PATCH',token=muni,data={'activo':False},expected=409)
        self.call(endpoint,method='DELETE',token=muni,expected=409)
        self.assertEqual(self.call('/admin/dashboard',token=muni)['stats']['streets'],1)
        self.assertEqual(self.call('/admin/dashboard',token=muni)['stats']['on_route'],1)
        self.assertEqual(self.call('/resident',token=vecino)['routes'][0]['phase'],'running')
        self.call(run,method='POST',token=driver,data={'accion':'pause'})
        self.call(run,method='POST',token=driver,data={'accion':'resume'})
        self.call(run,method='POST',token=driver,data={'accion':'finish'})
        self.call(endpoint,method='PUT',token=muni,data={**route,'desde':'08:00'})
        announcement={'categoria':'Cambio de horario','titulo':'Turno actualizado','mensaje':'Aviso de prueba'}
        self.call('/admin/announcements',method='POST',token=muni,data={**announcement,'district':'saylla'},expected=403)
        self.call('/admin/announcements',method='POST',token=muni,data=announcement)
        self.stop_api()
        self.start_api()
        data=self.call('/resident',token=vecino)
        self.assertEqual(data['announcements'][0]['title'],
                         'Turno actualizado')
        self.assertEqual(data['routes'][0]['start_time'],'08:00')
        self.assertEqual(data['routes'][0]['truck_name'],'Vehículo de prueba')
        self.assertEqual(data['routes'][0]['points'],[[20,30],[60,70]])
        self.assertNotIn('driver',data['routes'][0])
        self.assertEqual(self.call('/resident',token=vecino_foreign),{'routes':[],'announcements':[]})
        self.call(endpoint,method='DELETE',token=muni)
        self.assertEqual(self.call('/resident',token=vecino)['routes'],[])
        self.call('/logout',method='POST',token=vecino,data={})
        self.call('/resident',token=vecino,expected=401)


if __name__=='__main__':
    unittest.main()
