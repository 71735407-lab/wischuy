# Wischuy

Prototipo web de recolección domiciliaria en los ocho distritos de la provincia del Cusco. La demo original sigue disponible en el selector de perfiles. Las cuentas municipales, vecinales y de conductor conectadas consultan una base SQLite compartida mediante una API.

## Ejecutar localmente

Se necesita Python 3.10 o posterior con datos de zona horaria IANA. Desde la raíz del repositorio, para trabajar con un **inventario de demostración**:

```sh
python backend/server.py --db wischuy-demo.sqlite init-demo
python backend/server.py --db wischuy-demo.sqlite add-account municipality cusco muni.cusco "Municipalidad de prueba"
python backend/server.py --db wischuy-demo.sqlite add-account driver cusco conductor.cusco "Conductor de prueba"
python backend/server.py --db wischuy-demo.sqlite serve --web
```

Los comandos de cuentas solicitan contraseñas interactivamente. Abre [Wischuy en local](http://127.0.0.1:8000), inicia sesión como Municipalidad y asigna el conductor de prueba. En el perfil Vecino, selecciona **Consultar mi distrito**, elige Cusco y crea una cuenta vecinal con un usuario distinto. Puedes abrir otra pestaña o ventana para ver a la vez los perfiles. La consulta vecinal se actualiza automáticamente cada 15 segundos mientras la pestaña es visible y ofrece un botón de actualización manual.

El inventario inicial de la demo contiene tres camiones sin placa real por distrito. El comando `init-demo` requiere una base vacía. Para una operación municipal, utiliza otra base e incorpora el inventario real y las cuentas autorizadas. Las calles, referencias y sectores actuales proceden del catálogo del prototipo y deben validarse antes de usarlo como padrón oficial. El mapa reutiliza la cartografía de OpenStreetMap de Wischuy y permite dibujar el trazado sobre esa vista base. El trazado consiste en puntos manuales y no representa GPS en tiempo real.

## GitHub Pages y API

GitHub Pages sirve la interfaz estática. Despliega la API en un servidor con HTTPS y un volumen persistente para SQLite. En `municipal.config.js`, asigna a `window.WISCHUY_API_URL` la URL HTTPS pública del servicio, incluido el sufijo `/api`. Configura como origen CORS el dominio de Pages, por ejemplo `https://71735407-lab.github.io`, sin ruta ni barra final.

El comando `serve` usa un servidor de desarrollo. Para publicar, integra `backend.server.application(ruta_de_sqlite, conjunto_de_origenes)` con un servidor WSGI detrás de un proxy HTTPS. Inicializa primero la base con `add-account` o `init-demo`, según el entorno. Limita la frecuencia de solicitudes en el proxy, en especial para `/api/login` y `/api/neighbors`. Restringe el acceso al archivo SQLite y programa copias con la API de respaldo de SQLite. No publiques la base ni contraseñas en el repositorio.

El administrador técnico da de alta a los usuarios privilegiados con `add-account`. El registro web solo crea cuentas vecinales. El distrito y rol quedan vinculados a la cuenta. En la demo pública se puede cambiar de distrito y se limpian las selecciones anteriores. En el servicio conectado, acceder a otro distrito requiere iniciar sesión con una cuenta vinculada a dicho distrito. El autorregistro vecinal registra el distrito elegido, sin comprobación de domicilio. Los comunicados se muestran dentro de la aplicación.

## Verificar la API

```sh
python -m unittest backend.test_server -v
```

Las pruebas crean una base temporal y ejercitan el aislamiento entre distritos y perfiles, las altas y ediciones, la consulta vecinal, el seguimiento del conductor y la persistencia tras reiniciar el servidor.
