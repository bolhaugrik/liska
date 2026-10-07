// Az állomás valódi 3D-s képe (WebGL, three.js) a feltöltött modulmodellekkel.
// Ha a WebGL vagy a modellek betöltése nem sikerül, a régi, vászonra rajzolt nézet marad.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

const G = window.Game;
const GOLD = 0xf2b705, RED = 0xff5a4d, CYAN = 0x46c8eb, WHITE = 0xffffff;
const TYPE_OF = i => (i < 6 ? "gyar" : i < 16 ? "lako" : "uzlet");
const RING = 0.95, FLOOR = -0.5;
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = s => document.querySelector(s);

async function init() {
  const cv = $("#bg3d"), labelBox = $("#labels"), leader = $("#leader polyline");
  const renderer = new THREE.WebGLRenderer({ canvas: cv, antialias: true, powerPreference: "high-performance" });
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.2;
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0x02070e);
  scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.5;
  const camera = new THREE.PerspectiveCamera(34, 1, 0.05, 600);
  const sun = new THREE.DirectionalLight(0xfff1dc, 2.6); sun.position.set(-3, 4, 2.5); scene.add(sun);
  scene.add(new THREE.HemisphereLight(0x8fd8ff, 0x050d14, 0.4));
  const coreLight = new THREE.PointLight(CYAN, 2.4, 3.4, 1.5); coreLight.position.set(0, 0.3, 0); scene.add(coreLight);

  const loader = new GLTFLoader();
  const load = name => new Promise((ok, bad) => loader.load(`/models/${name}.glb`, g => {
    let mesh = null; g.scene.traverse(o => { if (o.isMesh && !mesh) mesh = o; });
    mesh ? ok(mesh) : bad(new Error(`nincs háló: ${name}`));
  }, undefined, bad));
  const [gyar, lako, uzlet, kozepe] = await Promise.all(["gyar", "lako", "uzlet", "kozepe"].map(load));
  const TEMPLATE = { gyar, lako, uzlet };
  for (const m of [gyar, lako, uzlet, kozepe]) { m.geometry.computeBoundingBox(); m.material.envMapIntensity = 1; }

  // ---- háttér: csillagok, bolygó, holografikus rács ----
  const starPos = new Float32Array(2200 * 3);
  for (let i = 0; i < 2200; i++) {
    const u = Math.random() * 2 - 1, a = Math.random() * 6.2832, r = Math.sqrt(1 - u * u);
    starPos.set([r * Math.cos(a) * 250, u * 250, r * Math.sin(a) * 250], i * 3);
  }
  const starGeo = new THREE.BufferGeometry(); starGeo.setAttribute("position", new THREE.BufferAttribute(starPos, 3));
  scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xcfe9ff, size: 1.7, sizeAttenuation: false, transparent: true, opacity: 0.85, depthWrite: false })));
  const planetGeo = new THREE.SphereGeometry(30, 128, 96), pp = planetGeo.attributes.position, puv = planetGeo.attributes.uv;
  for (let i = 0; i < pp.count; i++) puv.setXY(i, 0.5 + pp.getX(i) / 13, 0.5 + pp.getZ(i) / 13);   // felülről vetített kép: a látszó sapkán nincs torzulás
  const planetMat = new THREE.MeshStandardMaterial({ color: 0x0a2a3a, roughness: 1, metalness: 0, emissive: 0x020f17, envMapIntensity: 0 });
  const planet = new THREE.Mesh(planetGeo, planetMat);
  planet.position.set(0, -41.5, 0); scene.add(planet);
  new THREE.TextureLoader().load("/models/bolygo.jpg", tex => {          // a kapott bolygófelszín; ha nincs meg, marad az egyszínű
    tex.colorSpace = THREE.SRGBColorSpace; tex.wrapS = tex.wrapT = THREE.MirroredRepeatWrapping;
    tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    planetMat.map = tex; planetMat.color.setHex(0x9a9a9a); planetMat.emissive.setHex(0x000000); planetMat.needsUpdate = true; dirty = true;
    if (still) once();
  }, undefined, () => {});
  const air = new THREE.Mesh(new THREE.SphereGeometry(30.5, 96, 64), new THREE.ShaderMaterial({
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    vertexShader: "varying vec3 n; varying vec3 v; void main(){ n = normalize(normalMatrix * normal); vec4 p = modelViewMatrix * vec4(position, 1.0); v = normalize(-p.xyz); gl_Position = projectionMatrix * p; }",
    fragmentShader: "varying vec3 n; varying vec3 v; void main(){ float rim = pow(1.0 - abs(dot(normalize(n), normalize(v))), 6.0); gl_FragColor = vec4(vec3(0.24, 0.5, 1.0) * rim * 1.1, rim); }",
  }));
  air.position.copy(planet.position); scene.add(air);

  const lineMat = (color, opacity) => new THREE.LineBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false });
  const circlePts = (r, y, n = 96) => Array.from({ length: n + 1 }, (_, i) => new THREE.Vector3(r * Math.cos(i / n * 6.2832), y, r * Math.sin(i / n * 6.2832)));
  // az állomás külön csoport: ez forog, a bolygó és a csillagok a háttérben állnak (a kamera nem kering)
  const station = new THREE.Group(); scene.add(station);
  const grid = new THREE.Group(); station.add(grid);
  for (let r = 0.35; r <= 3.5; r += 0.28) {
    const key = Math.abs(r - 1.75) < 0.05;
    grid.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(circlePts(r, FLOOR)), lineMat(CYAN, key ? 0.5 : 0.15 * Math.max(0.2, 1 - r / 3.8))));
  }
  const rays = [];
  for (let i = 0; i < 24; i++) { const a = i / 24 * 6.2832; rays.push(new THREE.Vector3(0.35 * Math.cos(a), FLOOR, 0.35 * Math.sin(a)), new THREE.Vector3(3.5 * Math.cos(a), FLOOR, 3.5 * Math.sin(a))); }
  grid.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(rays), lineMat(CYAN, 0.07)));
  const ticks = [];
  for (let i = 0; i < 72; i++) { const a = i / 72 * 6.2832, r0 = i % 6 ? 1.7 : 1.64; ticks.push(new THREE.Vector3(r0 * Math.cos(a), FLOOR, r0 * Math.sin(a)), new THREE.Vector3(1.75 * Math.cos(a), FLOOR, 1.75 * Math.sin(a))); }
  grid.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(ticks), lineMat(CYAN, 0.5)));
  const sweep = new THREE.Group(); grid.add(sweep);
  for (let i = 0; i < 14; i++) {
    const a = -i * 0.03;
    sweep.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, FLOOR, 0), new THREE.Vector3(1.75 * Math.cos(a), FLOOR, 1.75 * Math.sin(a))]), lineMat(CYAN, 0.2 * (1 - i / 14))));
  }

  // ---- hazard-burok ----
  const shellMat = lineMat(CYAN, 0.1), shell = new THREE.Group(); station.add(shell);
  for (const [rx, rz] of [[0, 0], [Math.PI / 2, 0], [Math.PI / 2, Math.PI / 3], [Math.PI / 2, -Math.PI / 3]]) {
    const loop = new THREE.Line(new THREE.BufferGeometry().setFromPoints(circlePts(1.42, 0, 120)), shellMat);
    loop.rotation.set(rx, 0, rz); shell.add(loop);
  }
  shell.position.y = 0.08;

  // ---- központi mag és küllők ----
  const hubBox = kozepe.geometry.boundingBox, hubScale = 0.62 / (hubBox.max.x - hubBox.min.x);
  kozepe.scale.setScalar(hubScale); kozepe.position.set(0, -0.08, 0); kozepe.material.emissiveIntensity = 2.2; station.add(kozepe);
  // a magot és a gyűrűt nem kötik össze küllők: a mag külön forog, a forgalmat később űrjárművek jelzik

  // ---- a 24 modul ----
  const per = (360 - 3 * 7) / 24, units = [], picks = [];
  let ang = -90 + 3.5;
  for (let i = 0; i < 24; i++) {
    const type = TYPE_OF(i);
    if (i && TYPE_OF(i - 1) !== type) ang += 7;
    const a = (ang + per / 2) * Math.PI / 180; ang += per;
    const tpl = TEMPLATE[type], box = tpl.geometry.boundingBox, w = box.max.x - box.min.x, h = box.max.y - box.min.y, d = box.max.z - box.min.z;
    const s = RING * ((per - 1.2) * Math.PI / 180) / w;
    const group = new THREE.Group(); group.position.set(RING * Math.cos(a), 0, RING * Math.sin(a)); group.rotation.y = Math.PI / 2 - a; station.add(group);
    const mesh = new THREE.Mesh(tpl.geometry, tpl.material.clone()); mesh.scale.setScalar(s); group.add(mesh);
    const frameGeo = new THREE.EdgesGeometry(new THREE.BoxGeometry(w * s * 1.06, h * s * 1.08, d * s * 1.08));
    const frame = new THREE.LineSegments(frameGeo, new THREE.LineDashedMaterial({ color: CYAN, transparent: true, opacity: 0.9, dashSize: 0.012, gapSize: 0.009, depthTest: false }));
    frame.computeLineDistances(); frame.position.y = h * s / 2; frame.renderOrder = 5; group.add(frame);
    const beacon = new THREE.Group(); group.add(beacon);
    beacon.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, h * s, 0), new THREE.Vector3(0, h * s + 0.3, 0)]), lineMat(GOLD, 0.8)));
    const gem = new THREE.Mesh(new THREE.OctahedronGeometry(0.016), new THREE.MeshBasicMaterial({ color: GOLD })); gem.position.y = h * s + 0.3; beacon.add(gem);
    const ringMark = new THREE.Mesh(new THREE.TorusGeometry(0.03, 0.004, 8, 40), new THREE.MeshBasicMaterial({ color: GOLD })); ringMark.rotation.x = Math.PI / 2; ringMark.position.y = h * s + 0.09; group.add(ringMark);
    const plate = new THREE.Mesh(new THREE.BoxGeometry(w * s * 0.94, 0.006, d * s * 1.1), new THREE.MeshBasicMaterial({ color: CYAN, transparent: true, opacity: 0.85 }));
    plate.position.y = -0.012; group.add(plate);
    const pick = new THREE.Mesh(new THREE.BoxGeometry(w * s, h * s * 1.5, d * s * 1.2), new THREE.MeshBasicMaterial({ colorWrite: false, depthWrite: false }));
    pick.position.y = h * s * 0.75; pick.userData.id = i; group.add(pick); picks.push(pick);
    const label = document.createElement("div"); label.className = "lab"; label.textContent = String(i + 1); labelBox.appendChild(label);
    units.push({ id: i, type, a, group, mesh, frame, beacon, gem, ringMark, label, plate, s, h: h * s, top: new THREE.Vector3(RING * Math.cos(a), h * s, RING * Math.sin(a)), failed: false, mine: false, free: true });
  }

  // ---- kereskedőhajó: a bejelentéskor közelebb jön, érkezéskor a gyűrű mellé áll, távozáskor elrepül ----
  const gap = (units[5].a + units[6].a) / 2;                         // a gyárak és a lakók közti rés előtt parkol
  const at = (r, y, da) => new THREE.Vector3(r * Math.cos(gap + da), y, r * Math.sin(gap + da));
  const SHIP_POS = { far: at(6.5, 2.0, -0.6), near: at(2.7, 0.75, -0.28), dock: at(1.36, 0.12, 0), gone: at(8, -1.0, 0.8) };
  let ship = null, shipState = "gone", shipFrom = SHIP_POS.far.clone(), shipTo = SHIP_POS.far.clone(), shipT0 = -1e9;
  loader.load("/models/hajo.glb", g => {
    let m = null; g.scene.traverse(o => { if (o.isMesh && !m) m = o; });
    if (!m) return;
    m.geometry.computeBoundingBox(); const b = m.geometry.boundingBox, k = 0.5 / (b.max.x - b.min.x);
    m.scale.setScalar(k); m.rotation.y = Math.PI / 2; m.position.y = -(b.max.y - b.min.y) * k / 2;   // az orra (-X) előre néz
    m.material.emissiveIntensity = 2.0;
    ship = new THREE.Group(); ship.add(m); ship.visible = false; ship.position.copy(SHIP_POS.far); station.add(ship);
    dirty = true; if (still) once();
  }, undefined, () => {});
  function setShip(want) {
    shipFrom = (ship.visible ? ship.position : SHIP_POS.far).clone(); shipTo = SHIP_POS[want].clone();
    ship.visible = true; shipState = want; shipT0 = still ? -1e9 : clock;
  }
  function moveShip() {
    if (!ship || !ship.visible) return;
    const p = Math.min(1, (clock - shipT0) / 4200), e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
    ship.position.lerpVectors(shipFrom, shipTo, e);
    const ahead = p < 0.97 && shipFrom.distanceTo(shipTo) > 0.01 ? shipTo.clone()
      : ship.position.clone().add(new THREE.Vector3(-Math.sin(gap), 0, Math.cos(gap)));   // megérkezve a gyűrű érintője mentén áll
    ship.lookAt(station.localToWorld(ahead));
    if (p >= 1 && shipState === "gone") ship.visible = false;
    if (p >= 1 && shipState === "dock") ship.position.y = shipTo.y + 0.012 * Math.sin(clock / 900);   // finoman lebeg
  }

  // ---- kamera, vezérlés ----
  let yaw = 0.7, pitch = 0.62, zoom = 1, fit = 3, drag = null, aim = null, idleUntil = 0, clock = 0, last = 0, W = 1, H = 1, dirty = true, running = true;
  const target = new THREE.Vector3(0, 0.06, 0), ray = new THREE.Raycaster(), tmp = new THREE.Vector3();
  // álló képnél (mozgáscsökkentés) több kérésből is csak egy kirajzolás lesz
  let queued = false;
  const once = () => { if (queued) return; queued = true; requestAnimationFrame(t => { queued = false; frame(t); }); };
  function place() {
    const dist = fit * zoom, c = Math.cos(pitch);
    camera.position.set(0, dist * Math.sin(pitch) + target.y, dist * c); camera.lookAt(target);
    station.rotation.y = -yaw;
  }
  function measure() {
    W = innerWidth; H = innerHeight;
    renderer.setPixelRatio(Math.min(1.5, devicePixelRatio || 1)); renderer.setSize(W, H, false);
    const st = $("#stage"), r = !$("#game").hidden ? st.getBoundingClientRect() : null;
    let cx, cy, f;
    if (r && r.width > 120 && r.height > 120) { cx = r.left + r.width / 2; cy = r.top + r.height * 0.47; f = Math.min(r.width, r.height * 1.3); }
    else if (W > 1080) { cx = W * 0.29; cy = H * 0.5; f = Math.min(W * 0.55, H) * 0.95; }
    else { cx = W / 2; cy = H * 0.3; f = Math.min(W, H) * 0.9; }
    camera.aspect = W / H; camera.setViewOffset(W, H, W / 2 - cx, H / 2 - cy, W, H); camera.updateProjectionMatrix();
    fit = 1.14 * (H / 2) / Math.tan(camera.fov * Math.PI / 360) / (0.5 * f);
    dirty = true; if (still) once();
  }

  // ---- az állapot rávetítése a jelenetre ----
  function sync() {
    const S = G.S, live = !!(S && S.obs), me = live ? S.obs.me.id : -1, plan = G.plan, sel = live ? G.selected : null;
    const failed = new Set(live ? S.obs.last_events.filter(e => e.type === "failure").map(e => e.module) : []);
    const hz = live ? S.obs.station.hazard : 0.12, k = Math.min(1, hz / 0.8);
    shellMat.color.setRGB(0.27 + 0.73 * k, 0.78 - 0.43 * k, 0.92 - 0.62 * k); shellMat.opacity = 0.08 + 0.55 * hz;
    if (live && S.obs.station.crisis) { shellMat.color.setRGB(1.0, 0.6, 0.24); shellMat.opacity = Math.max(shellMat.opacity, 0.5); }   // válság: narancs burok
    for (const u of units) {
      const m = live ? S.obs.modules[u.id] : { type: u.type, holder: null, q: 0.9, level: 1 };
      if (m.type !== u.type && TEMPLATE[m.type]) { u.type = m.type; u.mesh.geometry = TEMPLATE[m.type].geometry; u.mesh.material = TEMPLATE[m.type].material.clone(); }
      const mat = u.mesh.material, free = m.holder === null, mine = m.holder === me, lv = m.level - 1;
      u.mesh.scale.set(u.s, u.s * (1 + 0.3 * lv), u.s * (1 + 0.16 * lv));
      mat.transparent = free; mat.opacity = free ? (live ? 0.3 : 0.55) : 1; mat.depthWrite = !free;
      mat.color.setScalar(free ? 0.75 : 0.3 + 0.7 * m.q);
      mat.emissiveIntensity = free ? 0.2 : 0.4 + 2.2 * m.q;
      u.failed = failed.has(u.id); u.mine = mine; u.free = free; u.selected = u.id === sel;
      const show = u.selected || mine || free || u.failed;
      u.frame.visible = show;
      u.frame.material.color.setHex(u.selected ? WHITE : u.failed ? RED : mine ? GOLD : CYAN);
      u.frame.material.gapSize = free && !u.selected ? 0.009 : 0; u.frame.material.opacity = free && !u.selected ? 0.45 : 0.95;
      u.frame.scale.set(1, 1 + 0.3 * lv, 1 + 0.16 * lv); u.frame.position.y = u.h * (1 + 0.3 * lv) / 2;
      u.beacon.visible = mine; u.beacon.position.y = u.h * 0.3 * lv;
      u.ringMark.visible = live && plan && plan.bid[u.id] > 0; u.ringMark.position.y = u.h * (1 + 0.3 * lv) + 0.09;
      u.top.y = u.h * (1 + 0.3 * lv);
      u.label.style.display = live ? "" : "none";
      const oc = live && !free ? G.ownerColor(m.holder) : null;       // a felirat és a talplemez a birtokos színét viseli
      u.plate.visible = !!oc; if (oc) u.plate.material.color.set(oc);
      u.label.style.background = oc || "transparent"; u.label.style.color = oc ? "#04121C" : "#9FC3D4";
      u.label.style.borderColor = oc ? "transparent" : "rgba(159,195,212,.7)";
    }
    const sh = live ? S.obs.station.ship : null, want = !sh ? "gone" : sh.here ? "dock" : "near";
    if (ship && want !== shipState) setShip(want);
    dirty = false;
  }

  function frame(now) {
    const dt = Math.min(60, now - last); last = now; clock += dt; const t = clock / 1000;
    if (aim) {
      const p = Math.min(1, (clock - aim.t0) / 700), e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
      yaw = aim.from + (aim.to - aim.from) * e; if (p >= 1) { aim = null; idleUntil = clock + 5000; }
    } else if (!drag && !still && clock > idleUntil) yaw += dt * 0.00012;
    if (dirty) sync();
    place();
    sweep.rotation.y = -t * 0.5; kozepe.rotation.y = t * 0.08;
    planet.rotation.y += dt * 0.000004;                              // a bolygó alig észrevehetően fordul
    coreLight.intensity = 2.2 + 0.5 * Math.sin(t * 1.7);
    const hz = G.S && G.S.obs ? G.S.obs.station.hazard : 0.12;
    shell.scale.setScalar(1 + 0.012 * Math.sin(t * (2 + 9 * hz)) * (0.3 + hz));
    for (const u of units) {
      if (u.mine) u.gem.scale.setScalar(1 + 0.35 * Math.sin(t * 3 + u.id));
      if (u.ringMark.visible) u.ringMark.scale.setScalar(1 + 0.25 * Math.sin(t * 4));
      if (u.failed && !u.selected) u.frame.material.opacity = 0.45 + 0.5 * Math.abs(Math.sin(t * 3.5));
    }
    moveShip();
    renderer.render(scene, camera);
    const camDist = camera.position.length();                       // feliratok és vezetővonal a kép fölött
    let line = "";
    for (const u of units) {
      if (u.label.style.display === "none") continue;
      tmp.copy(u.top); tmp.y += 0.035; tmp.applyMatrix4(station.matrixWorld); const d = tmp.distanceTo(camera.position); tmp.project(camera);
      const x = (tmp.x * 0.5 + 0.5) * W, y = (-tmp.y * 0.5 + 0.5) * H, near = Math.max(0, Math.min(1, (camDist + 0.7 - d) / 1.6));
      u.label.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) translate(-50%, -50%) scale(${(0.8 + 0.35 * near).toFixed(2)})`;
      u.label.style.opacity = (0.35 + 0.65 * near).toFixed(2);
      if (u.selected) {
        const card = $("#selInfo");
        if (card && !card.hidden) { const r = card.getBoundingClientRect(), ex = r.left + r.width / 2, above = r.top + r.height / 2 < y, ey = above ? r.bottom : r.top, my = (y + ey) / 2; line = `${x},${y + (above ? -8 : 8)} ${x},${my} ${ex},${my} ${ex},${ey}`; }
      }
    }
    leader.setAttribute("points", line);
    if (running && !still) requestAnimationFrame(frame);
  }

  function focus(id) {
    const u = units[id]; if (!u) return;
    let d = (Math.PI / 2 - u.a - yaw) % 6.2832; if (d > Math.PI) d -= 6.2832; if (d < -Math.PI) d += 6.2832;
    if (still) { yaw += d; return once(); }
    aim = { from: yaw, to: yaw + d, t0: clock };
  }
  function redraw() { dirty = true; if (still) once(); }
  function hits() {
    station.updateMatrixWorld(true);
    return units.map(u => { tmp.copy(u.top); tmp.y -= u.h * 0.4; tmp.applyMatrix4(station.matrixWorld); const z = tmp.distanceTo(camera.position); tmp.project(camera);
      const x = (tmp.x * 0.5 + 0.5) * W, y = (-tmp.y * 0.5 + 0.5) * H; return { id: u.id, z, poly: [[x - 4, y - 4], [x + 4, y - 4], [x + 4, y + 4], [x - 4, y + 4]] }; });
  }

  cv.addEventListener("pointerdown", e => { drag = { x: e.clientX, y: e.clientY, yaw, pitch, moved: false }; aim = null; cv.setPointerCapture(e.pointerId); cv.classList.add("drag"); });
  cv.addEventListener("pointermove", e => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
    yaw = drag.yaw - dx * 0.006; pitch = Math.max(0.1, Math.min(1.45, drag.pitch + dy * 0.005)); if (still) once();
  });
  const drop = e => {
    const d = drag; drag = null; cv.classList.remove("drag"); idleUntil = clock + 4000;
    const S = G.S;
    if (!d || d.moved || e.type !== "pointerup" || !(S && S.obs) || S.finished) return;
    ray.setFromCamera(new THREE.Vector2(e.clientX / W * 2 - 1, -(e.clientY / H) * 2 + 1), camera);
    if (ship && ship.visible && ray.intersectObject(ship, true).length) return G.showShip();   // a hajóra kattintva a doboza jön elő
    const hit = ray.intersectObjects(picks, false)[0];
    if (hit) G.select(hit.object.userData.id, true); else if (G.selected !== null) G.select(G.selected, true);
  };
  cv.addEventListener("pointerup", drop); cv.addEventListener("pointercancel", drop);
  cv.addEventListener("wheel", e => { e.preventDefault(); zoom = Math.max(0.55, Math.min(1.9, zoom * (1 + e.deltaY * 0.0009))); if (still) once(); }, { passive: false });
  addEventListener("resize", measure);
  addEventListener("keydown", e => {
    if (e.target.matches("input, select, textarea, button") || document.querySelector("dialog[open]")) return;
    const k = { ArrowLeft: [-0.12, 0], ArrowRight: [0.12, 0], ArrowUp: [0, -0.08], ArrowDown: [0, 0.08] }[e.key];
    if (!k) return;
    e.preventDefault(); aim = null; idleUntil = clock + 4000; yaw += k[0]; pitch = Math.max(0.1, Math.min(1.45, pitch + k[1])); if (still) once();
  });

  // ---- átvétel a vászonra rajzolt nézettől ----
  const old = window.Holo;
  if (old && old.stop) old.stop();
  $("#bg").hidden = true; cv.hidden = false; document.body.classList.add("gl");
  window.Holo = { start() {}, stop() { running = false; }, measure, redraw, focus, hits, webgl: true };
  measure(); last = performance.now(); requestAnimationFrame(frame);
}

init().catch(err => { console.warn("A 3D-s nézet nem indult el, marad a rajzolt változat:", err); });
