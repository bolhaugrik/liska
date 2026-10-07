"""Kész (már kis háromszögszámú) GLB modell előkészítése a játékhoz.

    python scripts/modell_beepit.py forras.glb liska/web/models/gyar.glb [textúraméret=1024]

- a textúrákat a megadott méretre kicsinyíti (a játékban egy modul legfeljebb pár száz képpont széles),
- a világos, telített színű részekből (izzó kohó, neon, ablakok) világító térképet készít,
- kiveszi a megjelenítéshez nem kellő kiterjesztéseket.
A geometriához nem nyúl. Kell hozzá:  pip install pillow numpy
"""
import io, json, struct, sys
import numpy as np
from PIL import Image


def jpeg(im, quality, sharp=False):
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=quality, subsampling=0 if sharp else 2); return buf.getvalue()


def process(src, dst, size=1024):
    d = open(src, "rb").read()
    jl = struct.unpack("<I", d[12:16])[0]; g = json.loads(d[20:20 + jl])
    bl = struct.unpack("<I", d[20 + jl:24 + jl])[0]; blob = d[28 + jl:28 + jl + bl]
    views = g["bufferViews"]; img_of = {im["bufferView"]: i for i, im in enumerate(g["images"])}
    mat = g["materials"][0]; pbr = mat["pbrMetallicRoughness"]
    src_of = lambda t: g["textures"][t["index"]]["source"]
    base_i = src_of(pbr["baseColorTexture"]); normal_i = src_of(mat["normalTexture"]) if "normalTexture" in mat else -1
    chunks, base = [], None
    for vi, bv in enumerate(views):
        raw = blob[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]
        if vi in img_of:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            if max(im.size) > size:
                im = im.resize((size, size), Image.LANCZOS)
            if img_of[vi] == base_i:
                base = im
            raw = jpeg(im, 92 if img_of[vi] == normal_i else 88, sharp=img_of[vi] == normal_i)
            g["images"][img_of[vi]]["mimeType"] = "image/jpeg"
        chunks.append(raw)
    a = np.asarray(base, np.float32); mx, mn = a.max(-1), a.min(-1); sat = (mx - mn) / np.maximum(mx, 1)
    glow = np.clip((mx / 255 - 0.58) / 0.25, 0, 1) * np.clip((sat - 0.38) / 0.25, 0, 1)
    emi = Image.fromarray(np.clip(a * glow[..., None], 0, 255).astype(np.uint8))
    views.append({"buffer": 0, "byteLength": 0}); chunks.append(jpeg(emi, 85))
    g["images"].append({"bufferView": len(views) - 1, "mimeType": "image/jpeg"})
    tex = {"source": len(g["images"]) - 1}
    if "sampler" in g["textures"][0]:
        tex["sampler"] = g["textures"][0]["sampler"]
    g["textures"].append(tex)
    mat["emissiveTexture"] = {"index": len(g["textures"]) - 1}; mat["emissiveFactor"] = [1.0, 1.0, 1.0]
    mat.pop("extensions", None); g.pop("extensionsUsed", None); g.pop("extensionsRequired", None)
    for m in g["meshes"]:
        for pr in m["primitives"]:
            pr.pop("extensions", None)
    out = bytearray()
    for bv, raw in zip(views, chunks):
        out += b"\0" * (-len(out) % 4)
        bv["byteOffset"] = len(out); bv["byteLength"] = len(raw); out += raw
    out += b"\0" * (-len(out) % 4)
    g["buffers"][0]["byteLength"] = len(out)
    js = json.dumps(g, separators=(",", ":")).encode(); js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(out)
    with open(dst, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total)); f.write(struct.pack("<I4s", len(js), b"JSON")); f.write(js)
        f.write(struct.pack("<I4s", len(out), b"BIN\0")); f.write(out)
    print(f"{dst}: {total / 1e6:.1f} MB, világító képpontok {float((glow > 0.3).mean()) * 100:.1f}%")


if __name__ == "__main__":
    process(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1024)
