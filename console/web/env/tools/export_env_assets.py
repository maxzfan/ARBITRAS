import bpy, os, sys, json, math, struct, base64
from mathutils import Vector, Matrix
sys.path.insert(0, os.path.expanduser("~/Desktop/UGV_CAD/environments"))
import build_env as BE
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "envexport")
os.makedirs(OUT, exist_ok=True)
MEASURED_U = {"forest": 0.633, "grassland": 0.6725, "urban": 0.3387, "dunes": 0.125}
PROTO = {"Tree": 4, "Bush": 2, "Fern": 1, "Rock": 3, "Bldg": 3, "Rubble": 2, "Lamp": 1, "Barrier": 1, "Tuft": 1}
N = 160; SIZE = BE.TERRAIN_SIZE
report = {}

def u_of(cam, d_world):
    d = cam.matrix_world.to_3x3().inverted() @ d_world
    lon = math.atan2(d.x, -d.z); lat = math.asin(max(-1.0, min(1.0, d.y)))
    return (lon / (2 * math.pi) + 0.5) % 1.0, math.degrees(lat)

for biome in ("forest", "grassland", "urban", "dunes"):
    bpy.ops.wm.open_mainfile(filepath=os.path.expanduser(f"~/Desktop/UGV_CAD/environments/ENV_{biome}.blend"))
    cfg = BE.BIOMES[biome]; hf = BE.make_height_fn(cfg)
    flat = []
    for iy in range(N):
        y = -SIZE / 2 + (iy + 0.5) * SIZE / N
        for ix in range(N):
            x = -SIZE / 2 + (ix + 0.5) * SIZE / N
            flat.append(float(hf(x, y)))
    lo, hi = min(flat), max(flat)
    cam = bpy.data.objects.get("PanoCam"); sun = bpy.data.objects.get("Sun")
    info = {"cam_loc": list(cam.matrix_world.translation) if cam else None,
            "cam_rot_deg": [round(math.degrees(a), 2) for a in cam.matrix_world.to_euler()] if cam else None,
            "pano_type": getattr(cam.data, "panorama_type", None) if cam else None}
    if cam and sun:
        d_world = (sun.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()
        u, el = u_of(cam, d_world)
        info.update({"sun_world": [round(v, 4) for v in d_world], "u_pred": round(u, 4), "el_pred": round(el, 1),
                     "u_measured": MEASURED_U[biome],
                     "u_of_plusX": round(u_of(cam, Vector((1, 0, 0)))[0], 4), "u_of_plusY": round(u_of(cam, Vector((0, 1, 0)))[0], 4)})
    # prototypes
    picks = []
    for pre, k in PROTO.items():
        objs = sorted([o for o in bpy.data.objects if o.type == 'MESH' and o.name.startswith(pre)], key=lambda o: o.name)
        if not objs: continue
        step = max(1, len(objs) // k)
        for i, o in enumerate(objs[::step][:k]): picks.append((pre, i, o))
    cols = {}
    for pre, i, o in picks:
        m = o.active_material; c = None
        if m and m.use_nodes:
            b = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if b and not b.inputs["Base Color"].is_linked: c = [round(v, 3) for v in b.inputs["Base Color"].default_value[:3]]
        cols.setdefault(pre, c)
    bpy.ops.object.select_all(action='DESELECT')
    dups = []
    for pre, i, o in picks:
        dup = o.copy(); dup.data = o.data.copy(); dup.name = f"{pre}_{i}"; dup.data.name = dup.name
        bpy.context.scene.collection.objects.link(dup); dup.parent = None
        dup.matrix_world = Matrix.Diagonal((*o.matrix_world.to_scale(), 1.0))
        target = 900 if pre == "Tree" else 2500          # acacia/street-tree silhouettes survive 900 faces; jungle trees are already ~600
        if len(dup.data.polygons) > target * 1.2:
            mod = dup.modifiers.new("dec", 'DECIMATE'); mod.ratio = target / len(dup.data.polygons)
        dup.select_set(True); dups.append(dup); bpy.context.view_layer.objects.active = dup
    glb = os.path.join(OUT, f"props_{biome}.glb")
    try:
        bpy.ops.export_scene.gltf(filepath=glb, use_selection=True, export_apply=True, export_yup=True,
                                  export_draco_mesh_compression_enable=True, export_materials='NONE',
                                  export_normals=True, export_texcoords=False, export_animations=False,
                                  export_skins=False, export_cameras=False, export_lights=False)
    except TypeError as e:
        print("export kwargs fallback:", e)
        bpy.ops.export_scene.gltf(filepath=glb, use_selection=True, export_apply=True,
                                  export_draco_mesh_compression_enable=True, export_materials='NONE')
    stats = {}
    for pre in PROTO:
        objs = [o for o in bpy.data.objects if o.type == 'MESH' and o.name.startswith(pre) and o not in dups]
        if not objs: continue
        rs = [(o.matrix_world.translation.xy).length for o in objs]
        sz = [o.matrix_world.to_scale().z for o in objs]
        dims = [max(o.dimensions) for o in objs]
        rmax = max(rs) or 1.0
        stats[pre] = {"n": len(objs), "r_max": round(rmax, 1), "density_per_m2": len(objs) / (math.pi * rmax * rmax),
                      "scale_min": round(min(sz), 2), "scale_max": round(max(sz), 2), "dim_median": round(sorted(dims)[len(dims)//2], 2),
                      "polys_each": round(sum(len(o.data.polygons) for o in objs) / len(objs)), "colour": cols.get(pre)}
    scale = (hi - lo) / 65000.0 if hi > lo else 1.0
    q = [int(round((v - lo) / scale)) - 32500 for v in flat]
    with open(os.path.join(OUT, f"relief_{biome}.json"), "w") as f:
        json.dump({"biome": biome, "n": N, "size_m": SIZE, "lo": lo, "hi": hi, "scale": scale, "offset": 32500,
                   "int16_le_b64": base64.b64encode(struct.pack("<%dh" % len(q), *q)).decode()}, f)
    info["height_range"] = [round(lo, 2), round(hi, 2)]; info["props"] = stats; info["glb_bytes"] = os.path.getsize(glb)
    info["protos"] = [f"{pre}_{i}" for pre, i, o in picks]
    report[biome] = info
    print("==", biome, json.dumps({k: v for k, v in info.items() if k != 'props'}, default=str))
    for pre, st in stats.items(): print("   ", pre, json.dumps(st, default=str))
json.dump(report, open(os.path.join(OUT, "report.json"), "w"), indent=1, default=str)
