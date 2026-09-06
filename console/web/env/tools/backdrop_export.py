# Runs the pack's export_env.py under real Blender with the GPU enabled and
# output redirected to a scratch dir. Usage:
#   Blender -b -P run_export.py -- <biome> [--hires|--test]
import sys, os, time
sys.path.insert(0, os.path.expanduser("~/Desktop/UGV_CAD/environments"))
import bpy
import export_env as EX

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
biomes = [a for a in args if not a.startswith("--")]
EX.SIM_DIR = os.environ.get("SIM_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hires"))
if "--hires" in args:
    EX.PANO_RES, EX.PANO_SAMPLES = (4096, 2048), 160
elif "--test" in args:
    EX.PANO_RES, EX.PANO_SAMPLES = (512, 256), 8

def enable_gpu():
    prefs = bpy.context.preferences.addons.get("cycles")
    if not prefs: return "CPU"
    cp = prefs.preferences
    for backend in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            cp.compute_device_type = backend
            cp.get_devices()
            for d in cp.devices: d.use = True
            return backend
        except (TypeError, AttributeError):
            continue
    return "CPU"

backend = enable_gpu()
print("cycles backend:", backend, "| pano", EX.PANO_RES, EX.PANO_SAMPLES, "| out", EX.SIM_DIR, flush=True)

_orig = EX.render_pano
def render_pano_gpu(path):
    scn = bpy.context.scene
    scn.cycles.device = 'GPU' if backend != "CPU" else 'CPU'
    return _orig(path)
EX.render_pano = render_pano_gpu

t0 = time.time()
for b in biomes:
    t1 = time.time()
    EX.export_biome(b)
    print("== %s done in %.0fs" % (b, time.time() - t1), flush=True)
print("all done in %.0fs" % (time.time() - t0), flush=True)
