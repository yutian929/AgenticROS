import * as THREE from 'three';
import { OrbitControls } from '/assets/OrbitControls.js';

const $ = id => document.getElementById(id);
let latest = null, selectedTopic = '/driver/joint_states', modelText = '', cloudStamp = '';
const frames = new Map(), robotVisuals = [], axes = [];
let renderer, scene, camera, controls, root, grid, cloud;
const vec = (s, fallback=[0,0,0]) => s ? s.trim().split(/\s+/).map(Number) : fallback;
const origin = (object, node) => {
  object.position.fromArray(vec(node?.getAttribute('xyz')));
  const [r,p,y] = vec(node?.getAttribute('rpy'));
  object.quaternion.setFromEuler(new THREE.Euler(r,p,y,'ZYX'));
};

function frame(name) {
  if (!frames.has(name)) {
    const group = new THREE.Group(); group.name = name; root.add(group); frames.set(name, group);
    const axis = new THREE.AxesHelper(name === 'base' ? .18 : .065);
    axis.visible = $('layer-tf').checked; group.add(axis); axes.push(axis);
  }
  return frames.get(name);
}

function initScene() {
  scene = new THREE.Scene(); scene.background = new THREE.Color('#0e1721');
  renderer = new THREE.WebGLRenderer({antialias:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2)); renderer.outputColorSpace = THREE.SRGBColorSpace;
  $('scene').append(renderer.domElement);
  camera = new THREE.PerspectiveCamera(43,1,.005,30); camera.up.set(0,0,1);
  controls = new OrbitControls(camera,renderer.domElement); controls.enableDamping = true; controls.minDistance=.15; controls.maxDistance=5;
  scene.add(new THREE.HemisphereLight(0xeaf4ff,0x465365,2.5));
  const light = new THREE.DirectionalLight(0xffffff,3); light.position.set(1,-1,3); scene.add(light);
  root = new THREE.Group(); scene.add(root); frame('base');
  grid = new THREE.GridHelper(2,20,0x446274,0x253849); grid.rotation.x=Math.PI/2; grid.position.z=-.001; root.add(grid);
  cloud = new THREE.Points(new THREE.BufferGeometry(),new THREE.PointsMaterial({size:.006,vertexColors:true,sizeAttenuation:true}));
  cloud.visible = false; root.add(cloud);
  const resize = () => { const r=$('scene').getBoundingClientRect(); renderer.setSize(r.width,r.height); camera.aspect=r.width/r.height; camera.updateProjectionMatrix(); };
  new ResizeObserver(resize).observe($('scene')); resize(); resetView();
  function draw(){requestAnimationFrame(draw); controls.update(); renderer.render(scene,camera);} draw();
}
function resetView(top=false) {
  camera.position.set(...(top ? [0,-.001,1.7] : [.95,-1.1,.85]));
  controls.target.set(0,0,.08); controls.update();
  $('perspective').classList.toggle('active',!top); $('top').classList.toggle('active',top);
}
function loadRobot(xml) {
  if (!xml || xml === modelText) return;
  const doc = new DOMParser().parseFromString(xml,'application/xml');
  if (doc.querySelector('parsererror')) throw new Error('URDF 解析失败');
  for(const mesh of robotVisuals){mesh.removeFromParent(); mesh.geometry.dispose(); mesh.material.dispose();} robotVisuals.length=0;
  const materials = new Map();
  for(const m of doc.querySelectorAll('robot > material')) materials.set(m.getAttribute('name'),vec(m.querySelector('color')?.getAttribute('rgba'),[.45,.55,.65,1]));
  for(const link of doc.querySelectorAll('robot > link')) {
    const group = frame(link.getAttribute('name'));
    for(const visual of link.querySelectorAll('visual')) {
      const box=visual.querySelector('geometry > box'), sphere=visual.querySelector('geometry > sphere'), cylinder=visual.querySelector('geometry > cylinder');
      let geometry;
      if(box) geometry=new THREE.BoxGeometry(...vec(box.getAttribute('size')));
      else if(sphere) geometry=new THREE.SphereGeometry(Number(sphere.getAttribute('radius')),24,16);
      else if(cylinder){geometry=new THREE.CylinderGeometry(Number(cylinder.getAttribute('radius')),Number(cylinder.getAttribute('radius')),Number(cylinder.getAttribute('length')),32); geometry.rotateX(Math.PI/2);}
      else continue;
      const m=visual.querySelector('material');
      const rgba=vec(m?.querySelector('color')?.getAttribute('rgba'),materials.get(m?.getAttribute('name'))||[.45,.55,.65,1]);
      const mesh=new THREE.Mesh(geometry,new THREE.MeshStandardMaterial({color:new THREE.Color(...rgba.slice(0,3)),roughness:.55,metalness:.2}));
      origin(mesh,visual.querySelector('origin')); group.add(mesh); robotVisuals.push(mesh);
    }
  }
  modelText=xml;
}
function updateTransforms(state) {
  // All transforms come from subscribed TF messages; no simulated object state is used.
  root.matrixAutoUpdate=true; root.position.set(0,0,0); root.quaternion.identity(); root.scale.set(1,1,1);
  for(const t of state.transforms) {
    if(t.child===t.parent) continue;
    const child=frame(t.child),parent=frame(t.parent);
    // Reject cyclic frame graphs received from an external publisher.
    let ancestor=parent,cycle=false; while(ancestor){if(ancestor===child){cycle=true;break;}ancestor=ancestor.parent;}
    if(cycle) continue;
    if(child.parent!==parent)parent.add(child);
    child.position.fromArray(t.translation); child.quaternion.fromArray(t.rotation);
  }
  root.updateMatrixWorld(true);
  const fixed=$('fixed-frame').value;
  if(fixed!=='base' && frames.has(fixed)) {
    const inverse=frames.get(fixed).matrixWorld.clone().invert();
    root.matrixAutoUpdate=false; root.matrix.copy(inverse); root.updateMatrixWorld(true);
  }
  const jointAge=state.watched['/driver/joint_states']?.age;
  robotVisuals.forEach(m=>m.visible=$('layer-robot').checked && jointAge!==null && jointAge<3);
  axes.forEach(a=>a.visible=$('layer-tf').checked);
  grid.visible=$('layer-grid').checked;
  const names=Array.from(frames.keys()).sort();
  if([...$('fixed-frame').options].map(x=>x.value).join('|')!==names.join('|')){
    $('fixed-frame').replaceChildren(...names.map(name=>new Option(name,name))); $('fixed-frame').value=names.includes(fixed)?fixed:'base';
  }
}
function updateCloud(data) {
  if(!cloud)return;
  if(JSON.stringify(data.stamp)!==cloudStamp){
    cloud.geometry.dispose(); cloud.geometry=new THREE.BufferGeometry();
    cloud.geometry.setAttribute('position',new THREE.Float32BufferAttribute(data.positions,3));
    const colors=new Float32Array(data.colors.length);
    // ROS colors are sRGB, while Three.js vertex colors use linear space.
    const c=new THREE.Color(); for(let i=0;i<colors.length;i+=3){c.setRGB(data.colors[i]/255,data.colors[i+1]/255,data.colors[i+2]/255,THREE.SRGBColorSpace);colors[i]=c.r;colors[i+1]=c.g;colors[i+2]=c.b;}
    cloud.geometry.setAttribute('color',new THREE.BufferAttribute(colors,3));
    frame(data.frame_id).add(cloud); cloudStamp=JSON.stringify(data.stamp);
  }
  cloud.visible=$('layer-cloud').checked;
  $('cloud-status').textContent=`${data.count.toLocaleString()} 点 · RGB-D 同步`;
}
function updateState(state){
  latest=state;
  const live=state.camera_age!==null && state.camera_age<3;
  $('indicator').classList.toggle('ok',live); $('connection').textContent=live?'ROS 数据已连接':'等待相机话题';
  $('camera-age').textContent=state.camera_age===null?'无数据':`${state.camera_age.toFixed(1)} s 前`;
  $('camera-image').style.opacity=live?'1':'.3';
  $('driver').textContent=state.driver.session_id?`${state.driver.recovery_required?'需要恢复':state.driver.busy?'执行中':'空闲'} · session ${state.driver.session_id}`:'等待 /driver/state…';
  $('joints').replaceChildren(...(state.joints.names||[]).map((name,i)=>{const row=document.createElement('div');row.className='joint'; const a=document.createElement('span'),b=document.createElement('span');a.textContent=name;b.textContent=(state.joints.positions[i]??0).toFixed(4);row.append(a,b);return row;}));
  const info=state.camera;
  $('calibration').textContent=info.k?`${info.width} × ${info.height} · RGB / Depth\nfx ${info.k[0].toFixed(1)} · fy ${info.k[4].toFixed(1)}\nframe: ${info.frame_id}`:'等待 CameraInfo…';
  const tf=document.createDocumentFragment();
  for(const t of state.transforms){const row=document.createElement('div');row.className='frame';row.textContent=`${t.parent} → ${t.child}`;tf.append(row);}
  $('frames').replaceChildren(tf);
  if(renderer){loadRobot(state.robot_description);updateTransforms(state);}
  renderTopics(); renderMessage();
}
function renderTopics(){
  if(!latest)return;
  const filter=$('topic-search').value.toLowerCase();
  $('topic-count').textContent=`${latest.topics.length} 个发现 · ${Object.keys(latest.watched).length} 个订阅`;
  const rows=latest.topics.filter(t=>`${t.name} ${t.types.join(' ')}`.toLowerCase().includes(filter)).map(t=>{
    const tr=document.createElement('tr');tr.dataset.topic=t.name;tr.classList.toggle('selected',selectedTopic===t.name);
    const w=latest.watched[t.name];
    const values=[t.name,t.types.join(', '),w?.error?'解码错误':w?.received?`${w.received} 条 · ${w.age}s`:w?'等待消息':t.supported?'点击订阅':'仅查看类型'];
    for(const value of values){const td=document.createElement('td');td.textContent=value;tr.append(td);}
    tr.addEventListener('click',async()=>{selectedTopic=t.name;renderTopics();renderMessage();if(t.supported && !latest.watched[t.name]){try{const r=await fetch('/api/watch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({topic:t.name})});if(!r.ok)throw new Error((await r.json()).error);}catch(e){$('message').textContent=e.message;}}});
    return tr;
  });
  $('topics').replaceChildren(...rows);
}
function renderMessage(){
  $('inspector-topic').textContent=selectedTopic;
  const record=latest?.watched[selectedTopic];
  $('message').textContent=record?record.error||JSON.stringify(record.summary,null,2):'此消息类型暂不支持内容预览。支持 Image、CameraInfo、JointState、TFMessage、String。';
}
$('topic-search').addEventListener('input',renderTopics);
$('perspective').onclick=()=>resetView();$('top').onclick=()=>resetView(true);$('reset').onclick=()=>resetView();
for(const id of ['layer-robot','layer-cloud','layer-tf','layer-grid'])$(id).addEventListener('change',()=>{if(latest&&renderer)updateTransforms(latest);if(cloud)cloud.visible=$('layer-cloud').checked&&latest?.camera_age<3;});
$('fixed-frame').addEventListener('change',()=>{if(latest&&renderer)updateTransforms(latest);});
try{initScene();}catch(e){$('scene-error').hidden=false;$('scene-error').textContent=`三维视图初始化失败：${e.message}。相机与话题面板仍可使用。`;renderer=null;}
async function poll(){
  try{const response=await fetch('/api/state');if(!response.ok)throw new Error('ROS 观察服务无响应');updateState(await response.json());}
  catch(e){$('indicator').classList.remove('ok');$('connection').textContent='连接中断，正在重连';if(cloud)cloud.visible=false;robotVisuals.forEach(m=>m.visible=false);}
  await Promise.allSettled([
    (async()=>{if(latest?.camera_age===null||latest?.camera_age>3)return;const r=await fetch('/frame.jpg');if(!r.ok)return;const url=URL.createObjectURL(await r.blob());const img=$('camera-image');const old=img.src;img.onload=()=>{if(old.startsWith('blob:'))URL.revokeObjectURL(old);};img.src=url;})(),
    (async()=>{if(!renderer)return;const r=await fetch('/api/cloud');const data=await r.json();if(!r.ok){cloud.visible=false;$('cloud-status').textContent=data.error;return;}updateCloud(data);})()
  ]);
  setTimeout(poll,500);
}
poll();
