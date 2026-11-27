/* District and role are determined by the API, not by values in the page. */
(() => {
  'use strict';
  const app = window.WischuyApp;
  const root = document.getElementById('screen');
  const esc = app.esc;
  const catalog = {};
  const days = ['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'];
  const categories = ['Aviso de recolección','Cambio de horario','Modificación de ruta',
    'Suspensión del servicio','Campaña de limpieza','Comunicado de emergencia'];
  const incidentTypes = ['Calle bloqueada','Vehículo averiado','Basura acumulada','Problema con residentes','Otro'];
  const sections = [
    ['overview','Resumen','home'], ['trucks','Camiones','truck'],
    ['routes','Rutas','map'], ['announcements','Comunicados','bell'], ['incidents','Incidencias','bell'],
  ];
  const labels = {available:'Disponible',maintenance:'En mantenimiento',on_route:'En ruta',
    running:'En ruta',paused:'En pausa',finished:'Recolección completada'};
  const profiles = {municipality:'Municipalidad',neighbor:'Vecino',driver:'Conductor'};
  const dateFormat = new Intl.DateTimeFormat('es-PE', {
    timeZone:'America/Lima', dateStyle:'medium', timeStyle:'short',
  });
  const state = {profile:'',account:null,data:null,district:'',section:'overview',location:'',
    editor:null,visibleMap:null,notice:'',error:'',busy:false,revision:0};
  let api = '';
  try {
    const value = window.WISCHUY_API_URL?.trim();
    if (value) {
      const url = new URL(value);
      if (url.protocol === 'https:' || (['localhost','127.0.0.1'].includes(url.hostname) && url.protocol === 'http:')) {
        api = url.href.replace(/\/$/,'');
      }
    }
  } catch { /* An invalid API URL leaves the existing demo available. */ }

  const sessionKey = profile => 'wischuy-session-' + profile;
  const stored = profile => sessionStorage.getItem(sessionKey(profile));
  const neighborLocation = 'wischuy-neighbor-location';
  const allowedLocations = district => [...new Set([...catalog[district].streets,...catalog[district].sectors])];
  function restoredLocation(account) {
    if(account.role!=='neighbor') return '';
    const [district,location]=(sessionStorage.getItem(neighborLocation)||'').split('\n');
    return district===account.district&&allowedLocations(district).includes(location)?location:'';
  }
  const btn = (label,action,id='',style='') =>
    '<button type="button" class="muni-btn '+style+'" data-muni="'+action+'"'+
    (id!==''?' data-item="'+esc(id)+'"':'')+'>'+esc(label)+'</button>';
  const opt = (value,label,selected) => '<option value="'+esc(value)+'"'+
    (String(value)===String(selected)?' selected':'')+'>'+esc(label)+'</option>';
  const input = (label,name,value='',type='text',attributes='required') =>
    '<label>'+esc(label)+'<input name="'+name+'" type="'+type+'" value="'+esc(value??'')+'" '+attributes+'></label>';
  const tag = (label,warning=false) => '<span class="muni-tag'+(warning?' warning':'')+'">'+esc(label)+'</span>';
  const message = () => (state.error?'<p class="muni-message error" role="alert">'+esc(state.error)+'</p>':'')+
    (state.notice?'<p class="muni-message" role="status">'+esc(state.notice)+'</p>':'');
  const heading = (title,description='',controls='') => '<div class="muni-title"><div><h1>'+esc(title)+'</h1>'+
    (description?'<p>'+esc(description)+'</p>':'')+'</div><div class="muni-actions">'+controls+'</div></div>';
  const districtName = () => catalog[state.account?.district || state.district]?.name || '';
  const period = route => route.days.map(day=>days[day-1]).join(', ')+' · '+route.start_time+'–'+route.end_time;
  const notices = () => '<div data-muni-notices>'+message()+'</div>';

  async function request(path, method='GET', body, profile=state.profile) {
    if (!api) throw new Error('El servicio municipal aún no está configurado en este sitio.');
    const token = stored(profile);
    let response;
    try {
      response = await fetch(api+path, {
        method, cache:'no-store',
        headers:{...(body ? {'Content-Type':'application/json'} : {}),
          ...(token ? {Authorization:'Bearer '+token} : {})},
        ...(body ? {body:JSON.stringify(body)} : {}),
        signal:AbortSignal.timeout(12000),
      });
    } catch {
      throw new Error('No se pudo conectar con el servicio municipal.');
    }
    const result = await response.json().catch(()=>({}));
    if (!response.ok) throw Object.assign(
      new Error(result.error || 'El servicio no pudo completar la operación.'),{status:response.status});
    return result;
  }

  function loadData(profile=state.profile) {
    return request({municipality:'/admin/dashboard',neighbor:'/resident',driver:'/driver/routes'}[profile],
      'GET',undefined,profile);
  }

  async function busy(action,protectedAction=false) {
    if (state.busy) return;
    const revision=state.revision;
    state.busy=true;
    state.error='';
    state.notice='';
    root.querySelectorAll('button,input,select,textarea').forEach(control=>{
      if (!control.disabled) {
        control.disabled=true;
        control.dataset.muniBusy='true';
      }
    });
    try {
      await action();
    } catch (error) {
      if (revision!==state.revision) return;
      state.error=error.message;
      if (protectedAction&&error.status===401) {
        sessionStorage.removeItem(sessionKey(state.profile));
        if(state.profile==='neighbor') sessionStorage.removeItem(neighborLocation);
        Object.assign(state,{account:null,data:null,editor:null});
        render();
      } else {
        const target=root.querySelector('[data-muni-notices]');
        if(target) target.innerHTML=message();
        else render();
      }
    } finally {
      if (revision===state.revision) {
        state.busy=false;
        root.querySelectorAll('[data-muni-busy]').forEach(control=>{
          control.disabled=false;
          delete control.dataset.muniBusy;
        });
      }
    }
  }

  async function open(profile) {
    const revision=++state.revision;
    Object.assign(state,{profile,account:null,data:null,district:'',section:'overview',location:'',
      editor:null,visibleMap:null,incidents:null,notice:'',error:'',busy:false,register:false});
    app.mount();
    render();
    if (!api) return;
    await busy(async()=>{
      if (!Object.keys(catalog).length) Object.assign(catalog,await request('/districts'));
      if (revision!==state.revision) return;
      if (stored(profile)) {
        try {
          const account=await request('/me','GET',undefined,profile);
          if (account.role!==profile) throw Object.assign(new Error('Perfil inválido.'),{status:401});
          const data=await loadData(profile);
          if (revision!==state.revision) return;
          Object.assign(state,{account,data,district:account.district,location:restoredLocation(account)});
        } catch(error) {
          if(error.status!==401) throw error;
          sessionStorage.removeItem(sessionKey(profile));
          if(profile==='neighbor') sessionStorage.removeItem(neighborLocation);
        }
      }
      render();
    });
  }

  async function login(form,register=false) {
    const profile=state.profile;
    const payload={rol:profile,distrito:state.district,...Object.fromEntries(form)};
    const result=await request(register?'/neighbors':'/login','POST',payload,profile);
    sessionStorage.setItem(sessionKey(profile),result.token);
    const data=await loadData(profile);
    Object.assign(state,{account:result.account,district:result.account.district,data,register:false});
    render();
  }

  async function signInDriver(form) {
    await open('driver');
    if (!api||state.account) return;
    const username=root.querySelector('input[name="usuario"]');
    if(username) username.value=form.get('usuario')||'';
    const normalized=new FormData();
    normalized.set('usuario',form.get('usuario'));
    normalized.set('contraseña',form.get('password'));
    await busy(()=>login(normalized));
  }

  function accountView() {
    const profile=state.profile;
    const controls=btn('Volver a los perfiles','exit');
    if (!api) {
      return '<section class="muni-account">'+heading(profiles[profile],'Servicio municipal',controls)+
        '<p>El servicio conectado aún no está configurado. Puedes recorrer la demostración de Wischuy desde el selector de perfiles.</p></section>';
    }
    const districtSelect= profile==='neighbor' ?
      '<label>Distrito de tu cuenta<select id="muni-district" aria-label="Distrito de tu cuenta">'+opt('','Selecciona tu distrito',state.district)+
      Object.entries(catalog).map(([id,d])=>opt(id,d.name,state.district)).join('')+'</select></label>' : '';
    const form = profile==='neighbor'&&!state.district ? '' :
      '<form class="stack" data-muni-form="'+(state.register?'register':'login')+'">'+
      (state.register?input('Nombre','nombre','','text','required maxlength="100"'):'')+
      input('Usuario','usuario','','text','required maxlength="64" autocomplete="username"')+
      input('Contraseña','contraseña','','password',state.register?
        'required minlength="12" maxlength="128" autocomplete="new-password"':
        'required maxlength="128" autocomplete="current-password"')+
      '<button class="btn primary">'+(state.register?'Crear cuenta':'Ingresar')+'</button></form>';
    return '<section class="muni-account">'+heading(profiles[profile],
      profile==='neighbor'?'Selecciona el distrito vinculado a tu cuenta.':'Acceso con usuario y contraseña.',controls)+notices()+
      districtSelect+form+(profile==='neighbor'&&state.district?
        '<p class="hint">'+btn(state.register?'Ya tengo una cuenta':'Crear cuenta vecinal','toggle-register')+'</p>':'')+'</section>';
  }

  function table(headers,rows) {
    return '<div class="muni-scroll" tabindex="0" aria-label="Tabla desplazable"><table class="muni-table"><thead><tr>'+
      headers.map(title=>'<th scope="col">'+esc(title)+'</th>').join('')+'</tr></thead><tbody>'+
      rows.map(cells=>'<tr>'+cells.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
  }

  function trucksView() {
    const rows=state.data.trucks.map(t=>[
      '<strong>'+esc(t.code)+'</strong><br>'+esc(t.name)+(t.sample?' '+tag('Demostración',true):''),
      esc(t.plate||'Sin placa real'),esc(t.driver||'Sin asignar'),tag(t.active?labels[t.status]:'Desactivado',!t.active),
      btn('Editar','edit-truck',t.id)+' '+btn(t.active?'Desactivar':'Activar','toggle-truck',t.id),
    ]);
    return heading('Gestión de camiones',districtName(),btn('Registrar camión','new-truck','','primary'))+
      (rows.length?table(['Vehículo','Placa','Conductor','Estado','Acciones'],rows):'<p>Este distrito aún no tiene camiones registrados.</p>');
  }

  function routesView() {
    const rows=state.data.routes.map(r=>[
      '<strong>'+esc(r.name)+'</strong><br>'+esc(r.streets.join(', ')),
      esc(period(r)),esc(r.truck_code+' · '+r.driver),
      tag(r.active?(labels[r.phase]||'Activa'):'Inactiva',!r.active),
      btn('Editar','edit-route',r.id)+' '+btn('Mapa','view-map',r.id)+' '+
      btn('Eliminar','archive-route',r.id,'danger'),
    ]);
    const route=state.data.routes.find(r=>r.id===state.visibleMap);
    return heading('Gestión de rutas',districtName(),btn('Crear ruta','new-route','','primary'))+
      (rows.length?table(['Ruta y calles','Días y horario','Asignación','Estado','Acciones'],rows):'<p>Aún no hay rutas registradas.</p>')+
      (route?'<article class="muni-card"><h2>'+esc(route.name)+'</h2>'+mapMarkup(route.points)+
        '<p class="muni-note">'+(route.points.length?'Trazado guardado por el municipio.':'La ruta aún no tiene un trazado guardado.')+
        ' Cartografía © OpenStreetMap.</p></article>':'');
  }

  function announcementCards(list,canDelete=false) {
    return list.length?list.map(a=>'<article class="muni-card">'+tag(a.category)+
      '<h3>'+esc(a.title)+'</h3><p>'+esc(a.message)+'</p><time datetime="'+esc(a.published_at)+'">'+
      esc(dateFormat.format(new Date(a.published_at)))+'</time> · '+esc(catalog[a.district].name)+
      (canDelete ? '<div class="muni-actions">'+btn('Eliminar','delete-announcement',a.id,'danger')+'</div>' : '')+
      '</article>').join(''):
      '<p>No hay comunicados publicados para este distrito.</p>';
  }

  function overview() {
    const fields=[['trucks','Total de camiones'],['available','Camiones disponibles'],['on_route','Camiones en ruta'],
      ['routes','Rutas activas'],['streets','Calles atendidas hoy'],['announcements','Comunicados publicados']];
    return heading('Resumen del distrito',districtName(),btn('Actualizar','refresh'))+
      '<div class="muni-grid">'+fields.map(([key,label])=>'<div class="muni-stat"><span>'+label+
        '</span><strong>'+state.data.stats[key]+'</strong></div>').join('')+'</div>'+
      (state.data.trucks.some(t=>t.sample)?'<p class="muni-note">El inventario incluye vehículos identificados como demostración.</p>':'');
  }

  function driverChoices(selected,optional=false) {
    return opt('',optional?'Sin asignar':'Selecciona un conductor',selected)+
      state.data.drivers.map(d=>opt(d.id,d.name,selected)).join('');
  }

  function checkboxes(label,name,options,values=[]) {
    return '<fieldset><legend>'+label+'</legend><div class="muni-options">'+options.map(([value,caption])=>
      '<label><input type="checkbox" name="'+name+'" value="'+esc(value)+'"'+
      (values.includes(value)?' checked':'')+'>'+esc(caption)+'</label>').join('')+'</div></fieldset>';
  }

  const maps = new Map();
  function baseMap(name) {
    if (maps.has(name)) return maps.get(name);
    const base=app.mapBase(name);
    if (!base) return '';
    const template=document.createElement('template');
    template.innerHTML=base;
    const svg=template.content.querySelector('svg');
    svg.querySelectorAll('path[stroke],circle[stroke]').forEach(node=>{
      const stroke=node.getAttribute('stroke');
      if (['#0d5c43','#0d775d','#1d6092','#794282','#984b32','#55702e','#855f18','#7c466b'].includes(stroke)) {
        const outline=node.previousElementSibling;
        if(node.tagName.toLowerCase()==='path'&&outline?.getAttribute('stroke')==='#fff'
            &&outline.getAttribute('d')===node.getAttribute('d')) outline.remove();
        node.remove();
      }
    });
    svg.querySelectorAll('text').forEach(node=>{if(/^[123]$/.test(node.textContent.trim())) node.remove();});
    svg.setAttribute('aria-label','Mapa base de '+name);
    maps.set(name,svg.outerHTML);
    return svg.outerHTML;
  }

  function mapMarkup(points=[],interactive=false) {
    const base=baseMap(districtName());
    if (!base) return '';
    const markup=base+'<svg class="muni-trace" viewBox="0 0 340 260" aria-hidden="true"></svg>';
    return interactive?'<div data-muni="map-point" class="muni-map" aria-label="Mapa para dibujar la ruta">'+
      markup+'</div>':'<div class="muni-map" data-points="'+esc(JSON.stringify(points))+'">'+markup+'</div>';
  }

  function drawTrace(svg,points) {
    const coordinates=points.map(([x,y])=>[x*3.4,y*2.6]);
    svg.innerHTML='<polyline fill="none" stroke="#086248" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" points="'+
      coordinates.map(p=>p.join(',')).join(' ')+'"/>'+coordinates.map(([x,y])=>
      '<circle cx="'+x+'" cy="'+y+'" r="4" fill="#fff" stroke="#086248" stroke-width="2"/>').join('');
  }

  function editorView() {
    const {kind,record}=state.editor;
    const r=record||{};
    const save='<div class="muni-actions"><button class="muni-btn primary" type="submit">Guardar</button>'+
      btn('Cancelar','cancel-edit')+'</div></form>';
    if(kind==='truck'){
      return heading(record?'Editar camión':'Registrar camión',districtName())+
        '<form class="muni-form" data-muni-form="truck">'+input('Código','codigo',r.code,'text','required maxlength="30"')+
        input('Placa','placa',r.plate,'text',(r.sample?'':'required ')+'maxlength="20"')+
        input('Nombre o número','nombre',r.name,'text','required maxlength="100"')+
        '<label>Conductor asignado<select name="conductor" aria-label="Conductor asignado">'+driverChoices(r.driver_id??'',true)+'</select></label>'+
        '<label>Estado<select name="estado" aria-label="Estado">'+opt('available','Disponible',r.condition)+
        opt('maintenance','En mantenimiento',r.condition)+'</select></label>'+
        '<label class="check"><input type="checkbox" name="activo"'+(r.active!==0?' checked':'')+'>Camión activo</label>'+
        (r.status==='on_route'?'<p>El estado En ruta se actualiza desde el recorrido del conductor.</p>':'')+save;
    }
    if(kind==='route'){
      const place=catalog[state.account.district];
      return heading(record?'Editar ruta':'Crear ruta',districtName())+
        '<form class="muni-form" data-muni-form="route">'+
        input('Nombre de la ruta','nombre',r.name,'text','required maxlength="110"')+
        checkboxes('Calles y referencias','calles',place.streets.map(s=>[s,s]),r.streets)+
        checkboxes('Sectores','sectores',place.sectors.map(s=>[s,s]),r.sectors)+
        checkboxes('Días de recolección','dias',days.map((d,i)=>[i+1,d]),r.days)+
        '<div class="muni-actions">'+input('Desde','desde',r.start_time,'time')+
        input('Hasta','hasta',r.end_time,'time')+'</div>'+
        '<label>Camión<select name="camion" id="muni-route-truck" aria-label="Camión" required>'+opt('','Selecciona un camión',r.truck_id)+
        state.data.trucks.map(t=>opt(t.id,t.code+' · '+t.name+(t.active?'':' · Desactivado'),r.truck_id)).join('')+'</select></label>'+
        '<label>Conductor responsable<select name="conductor" aria-label="Conductor responsable" required>'+driverChoices(r.driver_id)+'</select></label>'+
        '<div><strong>Trazado sobre el mapa (opcional)</strong><p class="muni-note">Pulsa el mapa para unir puntos. El trazado es manual.</p>'+
        mapMarkup([],true)+'<div class="muni-actions">'+btn('Quitar último punto','undo-point')+
        btn('Borrar trazado','clear-points')+'<span id="muni-points" class="muni-note" aria-live="polite"></span></div>'+
        '<a class="muni-note" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">Cartografía © OpenStreetMap</a></div>'+
        '<label class="check"><input type="checkbox" name="activa"'+(r.active!==0?' checked':'')+'>Ruta activa</label>'+save;
    }
    return heading('Publicar comunicado',districtName())+
      '<form class="muni-form" data-muni-form="announcement"><label>Tipo de comunicado<select name="categoria" aria-label="Tipo de comunicado" required>'+
      categories.map(s=>opt(s,s)).join('')+'</select></label>'+input('Título','titulo','','text','required maxlength="120"')+
      '<label>Mensaje<textarea name="mensaje" required maxlength="2000"></textarea></label>'+save;
  }

  function incidentsView() {
    const list=state.incidents;
    const content=list===null?'<p>Consultando las incidencias del distrito…</p>':
      list.length ? list.map(i=>'<article class="muni-card">'+tag(i.category)+'<h3>'+esc(i.route)+'</h3>'+
        '<p>'+esc(i.message)+'</p><p class="muni-note">'+esc(i.driver)+' · <time datetime="'+esc(i.reported_at)+'">'+
        esc(dateFormat.format(new Date(i.reported_at)))+'</time></p></article>').join(''):
        '<p>Aún no hay incidencias registradas en este distrito.</p>';
    return heading('Incidencias de conductores',districtName(),btn('Actualizar','refresh'))+content;
  }

  function adminView() {
    const items=sections.map(([key,label,symbol])=>'<button type="button" data-muni="'+key+'"'+
      (key===state.section?' aria-current="page"':'')+'>'+app.icon(symbol)+esc(label)+'</button>').join('');
    const content=state.editor?editorView():{
      overview,trucks:trucksView,routes:routesView,incidents:incidentsView,
      announcements:()=>heading('Comunicados',districtName(),btn('Publicar comunicado','new-announcement','','primary'))+
        announcementCards(state.data.announcements,true),
    }[state.section]();
    return '<div class="muni-layout"><nav class="muni-nav" aria-label="Panel municipal"><h2>Municipalidad</h2><p>'+
      esc(districtName())+'</p>'+items+'<button type="button" data-muni="logout">Cerrar sesión</button></nav>'+
      '<section class="muni-main">'+notices()+content+'</section></div>';
  }

  function residentRoutes() {
    if(!state.location) return '<p>Elige tu calle o sector para ver los recorridos correspondientes.</p>';
    const list=state.data.routes.filter(route=>route.streets.includes(state.location)||route.sectors.includes(state.location));
    return list.length?list.map(route=>routeCard(route)).join(''):'<p>Todavía no hay una ruta activa para esta ubicación.</p>';
  }

  function residentView() {
    const place=catalog[state.account.district];
    const choices=allowedLocations(state.account.district);
    return '<section class="muni-account">'+heading('Recolección en tu distrito',place.name,
      btn('Cerrar sesión','logout'))+notices()+
      '<label>Calle, referencia o sector<select id="muni-location" aria-label="Calle, referencia o sector">'+opt('','Selecciona tu calle o sector',state.location)+
      choices.map(value=>opt(value,value,state.location)).join('')+'</select></label>'+
      '<h2>Rutas y horarios</h2><div id="muni-resident-routes">'+residentRoutes()+'</div>'+
      '<div class="muni-title"><h2>Comunicados del distrito</h2>'+btn('Actualizar información','refresh')+'</div>'+
      '<div id="muni-resident-announcements">'+announcementCards(state.data.announcements)+'</div></section>';
  }

  function incidentForm(route) {
    return '<form class="muni-form" data-muni-form="incident"><h2>Reportar incidencia</h2><p>'+esc(route.name)+'</p>'+
      '<label>Tipo de incidencia<select name="categoria" required aria-label="Tipo de incidencia">'+
      opt('','Selecciona un tipo','')+incidentTypes.map(type=>opt(type,type)).join('')+'</select></label>'+
      '<label>Descripción<textarea name="mensaje" required maxlength="1000" rows="3"></textarea></label>'+
      '<div class="muni-actions"><button class="muni-btn primary" type="submit">Enviar incidencia</button>'+
      btn('Cancelar','cancel-edit')+'</div></form>';
  }

  function routeCard(route,driver=false) {
    const inService=['running','paused'].includes(route.phase);
    const truckStatus=!route.truck_active?'Camión desactivado':
      route.truck_condition==='maintenance'?'Camión en mantenimiento':'Programada';
    const controls=driver ? '<div class="muni-actions">'+
      (!route.phase?btn('Iniciar recorrido','run-start',route.id,'primary'):'')+
      (inService?btn(route.phase==='running'?'Pausar':'Reanudar','run-'+(route.phase==='running'?'pause':'resume'),route.id)+
        btn('Finalizar recorrido','run-finish',route.id):'')+
      btn('Reportar incidencia','report-incident',route.id)+'</div>' : '';
    return '<article class="muni-card"><h3>'+esc(route.name)+'</h3>'+tag(labels[route.phase]||truckStatus)+
      '<p><strong>Horario:</strong> '+esc(period(route))+'</p><p><strong>Camión:</strong> '+
      esc(route.truck_code+' · '+route.truck_name+(route.truck_plate?' · '+route.truck_plate:''))+
      (route.truck_sample?' '+tag('Demostración',true):'')+'</p>'+
      (driver||route.points.length ? mapMarkup(route.points)+'<p class="muni-note">'+
        (route.points.length?'Trazado municipal':'Mapa del distrito. Esta ruta aún no tiene trazado municipal.')+
        ' · Cartografía © OpenStreetMap</p>' : '')+
      '<ul>'+route.streets.map((street,index)=>'<li>'+esc(street)+
      (route.attended.includes(street)?' · Atendida':driver&&inService?' '+btn('Marcar atendida','attend-street',route.id+':'+index):'')+
      '</li>').join('')+'</ul>'+controls+
      (driver&&state.editor?.kind==='incident'&&state.editor.record.id===route.id?incidentForm(route):'')+'</article>';
  }

  function driverView() {
    return '<section class="muni-account">'+heading('Rutas asignadas',districtName(),btn('Cerrar sesión','logout'))+
      notices()+(state.data.routes.length?state.data.routes.map(r=>routeCard(r,true)).join(''):'<p>Aún no tienes rutas asignadas.</p>')+
      '<div class="muni-title"><h2>Avisos del distrito</h2>'+btn('Actualizar','refresh')+'</div>'+
      announcementCards(state.data.announcements)+'</section>';
  }

  function drawMaps(container=root) {
    container.querySelectorAll('.muni-map[data-points]').forEach(node=>
      drawTrace(node.querySelector('.muni-trace'),JSON.parse(node.dataset.points)));
  }

  function render() {
    root.classList.add('municipal-screen');
    root.closest('.shell').classList.toggle('municipal-shell',state.account?.role==='municipality');
    root.innerHTML=state.account&&state.data ?
      ({municipality:adminView,neighbor:residentView,driver:driverView}[state.profile])() : accountView();
    drawMaps();
    updateEditorTrace();
  }

  function updateResident() {
    const routes=root.querySelector('#muni-resident-routes');
    routes.innerHTML=residentRoutes();
    drawMaps(routes);
    root.querySelector('#muni-resident-announcements').innerHTML=announcementCards(state.data.announcements);
  }

  function updateEditorTrace() {
    const node=root.querySelector('[data-muni="map-point"] .muni-trace');
    if(!node) return;
    const points=state.editor.points;
    drawTrace(node,points);
    root.querySelector('#muni-points').textContent=points.length+' puntos';
  }

  function startEditor(kind,record) {
    state.editor={kind,record,points:record?.points?.slice()||[]};
    state.error='';
    state.notice='';
    render();
    root.querySelector('form input, form select')?.focus();
  }

  async function refresh(notice='') {
    if(state.profile==='municipality'&&state.section==='incidents') {
      state.incidents=(await request('/admin/incidents')).incidents;
    } else {
      state.data=await loadData();
    }
    state.notice=notice;
    state.error='';
    render();
  }

  function mutate(path,method,body,notice,closeEditor=false) {
    return busy(async()=>{
      await request(path,method,body);
      if(closeEditor) state.editor=null;
      try {
        await refresh(notice);
      } catch(error) {
        state.notice=notice;
        state.error='La operación se guardó. '+error.message;
        render();
      }
    },true);
  }

  function action(name,id,event,control) {
    const items=state.data;
    const number=Number(id);
    if(name==='exit'){state.revision++;return app.leave();}
    if(name==='logout'){
      return busy(async()=>{
        try { await request('/logout','POST',{}); } catch(error) { if(error.status!==401) throw error; }
        sessionStorage.removeItem(sessionKey(state.profile));
        if(state.profile==='neighbor') sessionStorage.removeItem(neighborLocation);
        state.revision++;
        Object.assign(state,{account:null,data:null,editor:null});
        app.leave();
      });
    }
    if(name==='toggle-register'){state.register=!state.register;state.error='';return render();}
    if(name==='refresh') return busy(()=>refresh(),true);
    if(sections.some(([key])=>key===name)) {
      Object.assign(state,{section:name,editor:null,visibleMap:null,error:'',notice:''});
      render();
      return busy(()=>refresh(),true);
    }
    if(name==='report-incident') {
      const route=items.routes.find(r=>r.id===number);
      if(route) startEditor('incident',route);
      return;
    }
    if(name==='view-map'){state.visibleMap=number;return render();}
    if(name==='cancel-edit'){state.editor=null;state.error='';return render();}
    if(['new-truck','new-route','new-announcement'].includes(name)) return startEditor(name.slice(4));
    if(name==='edit-truck') return startEditor('truck',items.trucks.find(t=>t.id===number));
    if(name==='edit-route') return startEditor('route',items.routes.find(r=>r.id===number));
    if(name==='toggle-truck') {
      const truck=items.trucks.find(t=>t.id===number);
      return mutate('/admin/trucks/'+id,'PATCH',{activo:!truck.active},'Estado del camión actualizado.');
    }
    if(name==='delete-announcement'){
      if(!window.confirm('¿Eliminar este comunicado? Dejará de aparecer para los vecinos y conductores.')) return;
      return mutate('/admin/announcements/'+id,'DELETE',undefined,'Comunicado eliminado.');
    }
    if(name==='archive-route'&&window.confirm('¿Eliminar esta ruta de la consulta vecinal?')) {
      return mutate('/admin/routes/'+id,'DELETE',undefined,'Ruta eliminada.');
    }
    if(name==='map-point'&&event.detail>0&&state.editor.points.length<100){
      const r=control.getBoundingClientRect();
      const point=[(event.clientX-r.left)/r.width*100,(event.clientY-r.top)/r.height*100]
        .map(n=>Math.round(Math.min(100,Math.max(0,n))*10)/10);
      state.editor.points.push(point);
      return updateEditorTrace();
    }
    if(name==='undo-point'||name==='clear-points'){
      if(name==='undo-point') state.editor.points.pop(); else state.editor.points=[];
      return updateEditorTrace();
    }
    if(name.startsWith('run-')) {
      if(name==='run-finish'&&!window.confirm('¿Finalizar el recorrido de hoy?')) return;
      return mutate('/driver/routes/'+id+'/run','POST',{accion:name.slice(4)},'Recorrido actualizado.');
    }
    if(name==='attend-street'){
      const [routeId,index]=id.split(':').map(Number);
      const route=items.routes.find(r=>r.id===routeId);
      return mutate('/driver/routes/'+routeId+'/run','POST',{accion:'attend',calle:route.streets[index]},'Calle marcada como atendida.');
    }
  }

  root.addEventListener('click',event=>{
    const control=event.target.closest('[data-muni]');
    if(control&&app.isConnected()&&!state.busy) action(control.dataset.muni,control.dataset.item,event,control);
  });

  root.addEventListener('change',event=>{
    if(!app.isConnected()) return;
    const {id,value,form}=event.target;
    if(id==='muni-district'){
      sessionStorage.removeItem(neighborLocation);
      Object.assign(state,{district:value,location:'',error:'',notice:'',register:false});
      render();
      root.querySelector('#muni-district')?.focus();
    }
    if(id==='muni-location'){
      const district=state.account.district;
      state.location=allowedLocations(district).includes(value)?value:'';
      sessionStorage.setItem(neighborLocation,district+'\n'+state.location);
      updateResident();
    }
    if(id==='muni-route-truck'){
      const truck=state.data.trucks.find(t=>t.id===Number(value));
      form.elements.conductor.value=truck?.driver_id?String(truck.driver_id):'';
    }
  });

  root.addEventListener('submit',event=>{
    const mode=event.target.dataset.muniForm;
    if(!mode||!app.isConnected()) return;
    event.preventDefault();
    const form=new FormData(event.target);
    const fields=Object.fromEntries(form);
    if(mode==='login'||mode==='register') return busy(()=>login(form,mode==='register'));
    if(mode==='announcement') return mutate('/admin/announcements','POST',fields,'Comunicado publicado.',true);
    const id=state.editor.record?.id;
    if(mode==='incident') return mutate('/driver/routes/'+id+'/incidents','POST',fields,'Incidencia enviada a la Municipalidad.',true);
    if(mode==='truck') return mutate('/admin/trucks'+(id?'/'+id:''),id?'PUT':'POST',{
      ...fields,conductor:Number(fields.conductor)||null,activo:form.has('activo'),
    },'Camión guardado.',true);
    if(mode==='route') return mutate('/admin/routes'+(id?'/'+id:''),id?'PUT':'POST',{
      nombre:fields.nombre,camion:Number(fields.camion),conductor:Number(fields.conductor),
      desde:fields.desde,hasta:fields.hasta,activa:form.has('activa'),calles:form.getAll('calles'),
      sectores:form.getAll('sectores'),dias:form.getAll('dias').map(Number),puntos:state.editor.points,
    },'Ruta guardada.',true);
  });

  setInterval(async()=>{
    if(!app.isConnected()||!state.account||state.profile!=='neighbor'||state.busy||document.hidden) return;
    const revision=state.revision;
    try {
      const data=await loadData();
      if(!app.isConnected()||state.revision!==revision||state.busy) return;
      if(JSON.stringify(data)===JSON.stringify(state.data)) return;
      state.data=data;
      updateResident();
    } catch { /* The manual refresh reports network and session errors. */ }
  },15000);

  window.WischuyConnected={open,render,signInDriver};
})();
