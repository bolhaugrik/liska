"""Nagy felbontású 3D modell átalakítása a játékhoz.

    python scripts/modell_atalakit.py eredeti.glb gyar_1.glb [háromszögszám=25000] [textúraméret=2048]

A képből 3D-t készítő generátorok több millió háromszöges, erősen széttöredezett textúrakiosztású
modellt adnak; azt a szokásos egyszerűsítők nem tudják 100-200 ezer háromszög alá vinni. Ez a szkript
ezért a régi kiosztástól függetlenül egyszerűsít, új kiosztást készít, és a színt, a fémességet, az
érdességet meg a domborzatot átsüti az új hálóra. A világos, telített színű részekből (izzó kohó,
ablakok, neon) világító térképet is készít, hogy a modell a sötét jelenetben is éljen.

Kell hozzá:  pip install trimesh fast-simplification pymeshlab scipy pillow numpy
Egy modell nagyjából másfél perc és 3 GB memória.
"""
import os, sys, time, io, numpy as np, trimesh
from PIL import Image
from scipy.spatial import cKDTree
from scipy import ndimage

SRC, OUT = sys.argv[1], sys.argv[2]
TARGET = int(sys.argv[3]) if len(sys.argv) > 3 else 25000
S = int(sys.argv[4]) if len(sys.argv) > 4 else 2048
t0 = time.time(); log = lambda *a: print(f"[{time.time() - t0:5.0f} mp]", *a, flush=True)

# ---- 1. forrás ----
src = trimesh.load(SRC, process=False, force="mesh")
V, F = np.asarray(src.vertices, np.float64), np.asarray(src.faces)
UV = np.asarray(src.visual.uv, np.float64); VN = np.asarray(src.vertex_normals, np.float64)
mat = src.visual.material
tex = lambda im: None if im is None else np.asarray(im.convert("RGB"), np.float32)
T_col, T_mr = tex(mat.baseColorTexture), tex(mat.metallicRoughnessTexture)
log(f"forrás: {len(F)} háromszög, {len(V)} csúcs")

# ---- 2. egyszerűsítés a régi textúrakiosztástól függetlenül ----
import fast_simplification
w = trimesh.Trimesh(V, F, process=False); w.merge_vertices(merge_tex=True, merge_norm=True)
lv, lf = fast_simplification.simplify(np.asarray(w.vertices, np.float32), np.asarray(w.faces, np.int32), target_count=TARGET, agg=5)
low = trimesh.Trimesh(lv.astype(np.float64), lf, process=True)
low.update_faces(low.nondegenerate_faces()); low.update_faces(low.unique_faces()); low.remove_unreferenced_vertices()
LV, LF, LN = np.asarray(low.vertices), np.asarray(low.faces), np.asarray(low.vertex_normals)
log(f"egyszerűsítve: {len(LF)} háromszög, {len(LV)} csúcs")

# ---- 3. új, háromszögenkénti textúrakiterítés ----
import pymeshlab
ms = pymeshlab.MeshSet(); ms.add_mesh(pymeshlab.Mesh(vertex_matrix=LV, face_matrix=LF.astype(np.int32)), "low")
ms.apply_filter("compute_texcoord_parametrization_triangle_trivial_per_wedge", sidedim=0, textdim=S, border=4, method="Space-optimizing")
WUV = ms.current_mesh().wedge_tex_coord_matrix().reshape(-1, 3, 2)          # v felfelé nő
PX = np.stack([WUV[..., 0] * S, (1 - WUV[..., 1]) * S], -1)                  # képpont-koordináták, y lefelé

# ---- 4. az új háromszögek kirajzolása az atlaszra: melyik képpont melyik térbeli pont ----
pos = np.zeros((S, S, 3), np.float32); nrm = np.zeros((S, S, 3), np.float32)
filled = np.zeros((S, S), bool); fid = np.zeros((S, S), np.int32)
P3 = LV[LF]; N3 = LN[LF]
for strict in (True, False):
  for i in range(len(LF)):
      a, b, c = PX[i]
      x0, x1 = int(np.floor(min(a[0], b[0], c[0]) - 1)), int(np.ceil(max(a[0], b[0], c[0]) + 1))
      y0, y1 = int(np.floor(min(a[1], b[1], c[1]) - 1)), int(np.ceil(max(a[1], b[1], c[1]) + 1))
      x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, S), min(y1, S)
      if x1 <= x0 or y1 <= y0: continue
      xs, ys = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
      det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
      if abs(det) < 1e-9: continue
      l0 = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / det
      l1 = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / det
      l2 = 1 - l0 - l1
      eps = 0.0 if strict else 1.5 / max(1.0, np.sqrt(abs(det)))   # 2. menet: kis ráhagyás a szélen túl
      m = (l0 >= -eps) & (l1 >= -eps) & (l2 >= -eps)
      if not m.any(): continue
      yy, xx = np.nonzero(m); yy += y0; xx += x0
      L = np.stack([l0[m], l1[m], l2[m]], -1)
      new = ~filled[yy, xx]
      yy, xx, L = yy[new], xx[new], L[new]
      pos[yy, xx] = L @ P3[i]; nn = L @ N3[i]; nrm[yy, xx] = nn / np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-9)
      fid[yy, xx] = i
      filled[yy, xx] = True
log(f"atlasz: {filled.mean() * 100:.0f}% kitöltve ({S}x{S})")

# ---- 5. forrásminták: sűrű pontfelhő a régi felületről, színnel és normállal ----
rng = np.random.default_rng(0)
area = src.area_faces; NS = 4_000_000
fi = rng.choice(len(F), NS, p=area / area.sum())
r1, r2 = np.sqrt(rng.random(NS)), rng.random(NS)
B = np.stack([1 - r1, r1 * (1 - r2), r1 * r2], -1)[..., None]
sp = (V[F[fi]] * B).sum(1).astype(np.float32); sn = (VN[F[fi]] * B).sum(1).astype(np.float32); sn /= np.maximum(np.linalg.norm(sn, axis=1, keepdims=True), 1e-9)
suv = (UV[F[fi]] * B).sum(1).astype(np.float32)
def sample(img, uv):
    h, wd = img.shape[:2]
    x = np.clip((uv[:, 0] % 1.0) * wd, 0, wd - 1).astype(int); y = np.clip((1 - uv[:, 1] % 1.0) * h, 0, h - 1).astype(int)
    return img[y, x]
s_col = sample(T_col, suv); s_mr = sample(T_mr, suv) if T_mr is not None else None
LAM = 0.004            # a normál csak kicsit számít: a túloldali felületet kizárja, a részletet nem simítja el
tree = cKDTree(np.hstack([sp, sn * LAM]).astype(np.float32))
T_nm = tex(mat.normalTexture)
if T_nm is not None:
    uvg = np.stack([UV[:, 0], 1 - UV[:, 1]], -1)                          # glTF-állás: v lefelé nő
    p0, p1, p2 = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]; d1, d2 = uvg[F[:, 1]] - uvg[F[:, 0]], uvg[F[:, 2]] - uvg[F[:, 0]]
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]; r = np.where(np.abs(r) < 1e-12, 1e-12, r)[:, None]
    fT = ((p1 - p0) * d2[:, 1:2] - (p2 - p0) * d1[:, 1:2]) / r           # dP/du
    fB = ((p2 - p0) * d1[:, 0:1] - (p1 - p0) * d2[:, 0:1]) / r           # dP/dv (lefelé)
    t = fT[fi]; t -= sn * (t * sn).sum(1, keepdims=True); t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    bq = fB[fi]; bq -= sn * (bq * sn).sum(1, keepdims=True) + t * (bq * t).sum(1, keepdims=True); bq /= np.maximum(np.linalg.norm(bq, axis=1, keepdims=True), 1e-9)
    c = sample(T_nm, suv) / 255 * 2 - 1
    sn = t * c[:, 0:1] - bq * c[:, 1:2] + sn * c[:, 2:3]; sn /= np.maximum(np.linalg.norm(sn, axis=1, keepdims=True), 1e-9)
    del fT, fB, t, bq, p0, p1, p2
log("forrásminták és keresőfa kész")
yy, xx = np.nonzero(filled)
_, nearest = tree.query(np.hstack([pos[yy, xx], nrm[yy, xx] * LAM]).astype(np.float32), k=1, workers=-1)
del tree; import gc; gc.collect()
log("átvitel kész")

col = np.zeros((S, S, 3), np.float32); mr = np.zeros((S, S, 3), np.float32); nm = np.zeros((S, S, 3), np.float32)
col[yy, xx] = s_col[nearest]
if s_mr is not None: mr[yy, xx] = s_mr[nearest]
# domborzattérkép abban az érintőrendszerben, amelyet a megjelenítő a képernyőn számol:
# T és B az u és a v felületi gradiense (nem merőlegesítve, közös léptékkel), a zöld csatorna fordított
E1 = (P3[:, 1] - P3[:, 0]).astype(np.float32); E2 = (P3[:, 2] - P3[:, 0]).astype(np.float32)
D1 = (PX[:, 1] - PX[:, 0]).astype(np.float32); D2 = (PX[:, 2] - PX[:, 0]).astype(np.float32)
N = nrm[yy, xx]; f_ = fid[yy, xx]
c2 = np.cross(E2[f_], N); c1 = np.cross(N, E1[f_])
T = c2 * D1[f_][:, 0:1] + c1 * D2[f_][:, 0:1]
Bv = c2 * D1[f_][:, 1:2] + c1 * D2[f_][:, 1:2]
sc = 1.0 / np.sqrt(np.maximum(np.maximum((T * T).sum(1), (Bv * Bv).sum(1)), 1e-20))[:, None]
T *= sc; Bv *= sc
h = sn[nearest].astype(np.float32)
h = np.where(((h * N).sum(1) < 0.1)[:, None], N, h)                # ami hátrafelé nézne, az a sima normált kapja
def normal_map(sx=1, sy=-1):
    out = np.zeros((S, S, 3), np.float32)
    m = np.tile(np.array([0.0, 0.0, 1.0], np.float32), (len(N), 1))
    for a0 in range(0, len(N), 400_000):                               # darabokban, hogy elférjen a memóriában
        sl = slice(a0, a0 + 400_000)
        M = np.stack([sx * T[sl], sy * Bv[sl], N[sl]], -1).astype(np.float64)   # oszlopok: T, B, N
        ok = np.abs(np.linalg.det(M)) > 1e-6
        part = m[sl]
        part[ok] = np.linalg.solve(M[ok], h[sl][ok].astype(np.float64)[..., None])[..., 0]
        m[sl] = part
    m[m[:, 2] < 0.05] = (0, 0, 1)
    m /= np.linalg.norm(m, axis=1, keepdims=True)
    out[yy, xx] = (m * 0.5 + 0.5) * 255
    return out

# ---- 6. a kitöltetlen képpontok a legközelebbi kitöltött értékét kapják (ne látsszanak varratok) ----
ind = ndimage.distance_transform_edt(~filled, return_distances=False, return_indices=True)
fill = lambda a: a[ind[0], ind[1]]
col, mr = fill(col), fill(mr)
# világító részek: az erősen telített, világos színek (izzó kohó, ablakok, neon) saját fényt kapnak
mx, mn = col.max(-1), col.min(-1); sat = (mx - mn) / np.maximum(mx, 1)
glow = np.clip((mx / 255 - 0.62) / 0.25, 0, 1) * np.clip((sat - 0.42) / 0.25, 0, 1)
emi = col * glow[..., None]
log(f"érdesség/fémesség átlaga: forrás {s_mr[:, 1].mean():.0f}/{s_mr[:, 2].mean():.0f}, átsütve {mr[yy, xx][:, 1].mean():.0f}/{mr[yy, xx][:, 2].mean():.0f}")
log(f"világító képpontok aránya: {(glow > 0.3).mean() * 100:.1f}%")

# ---- 7. kiírás ----
def jpg(a, q=90):
    buf = io.BytesIO(); Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=q, subsampling=0); buf.seek(0)
    im = Image.open(buf); im.load(); return im
verts = P3.reshape(-1, 3); faces = np.arange(len(verts)).reshape(-1, 3)
nm = fill(normal_map())
# a modell alul középre kerül: a talppont az origó, Y felfelé
lo, hi = verts.min(0), verts.max(0)
verts = verts - np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2])
material = trimesh.visual.material.PBRMaterial(
    baseColorTexture=jpg(col), metallicRoughnessTexture=jpg(mr) if s_mr is not None else None, normalTexture=jpg(nm, 95),
    emissiveTexture=jpg(emi), emissiveFactor=[1.0, 1.0, 1.0], metallicFactor=1.0, roughnessFactor=1.0, name="modul")
out = trimesh.Trimesh(verts, faces, vertex_normals=N3.reshape(-1, 3), process=False,
                      visual=trimesh.visual.TextureVisuals(uv=WUV.reshape(-1, 2), material=material))
out.export(OUT)
size = hi - lo
log(f"kész: {OUT}, {len(faces)} háromszög, {os.path.getsize(OUT) / 1e6:.1f} MB, méret {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} (szél. x mag. x mély.)")
