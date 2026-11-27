// La demo local usa su propia API. GitHub Pages se conecta a PythonAnywhere.
const isLocalWischuy = location.protocol === 'http:' && ['localhost','127.0.0.1'].includes(location.hostname);
window.WISCHUY_API_URL = isLocalWischuy ? location.origin + '/api' : location.protocol === 'https:' ? 'https://ruffy04.pythonanywhere.com/api' : '';
