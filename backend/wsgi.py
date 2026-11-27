"""WSGI entry point for hosting the shared municipal API."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from backend.server import application, init_db


def configure():
    path = Path(os.environ['WISCHUY_DB_PATH']).expanduser()
    if not path.is_absolute():
        raise ValueError('WISCHUY_DB_PATH debe ser una ruta absoluta en almacenamiento persistente.')
    origins = {value.strip() for value in os.environ['WISCHUY_ALLOWED_ORIGINS'].split(',') if value.strip()}
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or origin != f'https://{parsed.netloc}' or parsed.username:
            raise ValueError('Cada origen debe ser HTTPS y contener solo el dominio y el puerto opcional.')
    if not origins:
        raise ValueError('Configura el origen HTTPS de GitHub Pages.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    init_db(path)
    return application(path, origins)


app = configure()
