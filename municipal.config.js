// Configura aquí la URL HTTPS de la API para la publicación en GitHub Pages.
// Una cadena vacía mantiene disponible la demostración original.
const isLocalWischuy = location.protocol === 'http:' && ['localhost','127.0.0.1'].includes(location.hostname);
window.WISCHUY_API_URL = isLocalWischuy ? location.origin + '/api' : '';
