# Junta las teselas de todos los bloques, hace los niveles de lejos (13 a 9) y deja la web lista para publicar.
# Uso: python piramide.py carpeta_con_bloques carpeta_web
import json, os, re, sys, shutil
import numpy as np
from PIL import Image
ENT, SAL = sys.argv[1], sys.argv[2]
TOPE = 0.97e9                                   # GitHub Pages no admite webs de más de 1 GB
os.makedirs(os.path.join(SAL, 'teselas'), exist_ok=True)
T = {}
for f in os.listdir(ENT):
    m = re.match(r'14-(\d+)-(\d+)\.webp$', f)
    if m: T[(int(m.group(1)), int(m.group(2)))] = os.path.join(ENT, f)
print('teselas de nivel 14:', len(T), flush=True)
def carga(f):
    im = Image.open(f); return np.asarray(im.convert('RGBA')).astype(np.float32)
def mitad(a):
    al = a[..., 3:4] / 255; pre = a[..., :3] * al
    P = pre.reshape(512, 2, 512, 2, 3).mean(axis=(1, 3)); A = al.reshape(512, 2, 512, 2, 1).mean(axis=(1, 3))
    return np.dstack([np.clip(P / np.maximum(A, 1e-3), 0, 255), A * 255])
def guarda(a, f, q):
    a = (a + 0.5).astype(np.uint8)
    im = Image.fromarray(a[..., :3]) if a[..., 3].min() >= 254 else Image.fromarray(a, 'RGBA')
    im.save(f, 'WEBP', quality=q, method=6)
for (x, y), f in T.items(): shutil.copyfile(f, os.path.join(SAL, 'teselas', f'14-{x}-{y}.webp'))
niv = {k: v for k, v in T.items()}; z = 14; cuenta = {14: len(T)}
mit = {}
while z > 9:
    sig = {}
    padres = {(x // 2, y // 2) for x, y in niv}
    for X, Y in padres:
        C = np.zeros((1024, 1024, 4), np.float32)
        for dx in (0, 1):
            for dy in (0, 1):
                k = (X * 2 + dx, Y * 2 + dy)
                if k in niv:
                    a = mit[k] if k in mit else mitad(carga(niv[k]))
                    C[dy * 512:(dy + 1) * 512, dx * 512:(dx + 1) * 512] = a
        f = os.path.join(SAL, 'teselas', f'{z - 1}-{X}-{Y}.webp'); guarda(C, f, 84); sig[(X, Y)] = f
    mit = {}; niv = sig; z -= 1; cuenta[z] = len(niv); print('nivel', z, len(niv), flush=True)
def total(): return sum(os.path.getsize(os.path.join(SAL, 'teselas', q)) for q in os.listdir(os.path.join(SAL, 'teselas')))
tam = total(); print('peso', round(tam / 1e6), 'MB', flush=True)
q = 76
while tam > TOPE and q >= 60:                   # si no cabe, se comprime más el nivel de más detalle
    for (x, y) in T:
        f = os.path.join(SAL, 'teselas', f'14-{x}-{y}.webp'); guarda(carga(f), f, q)
    tam = total(); print('recomprimido a', q, '·', round(tam / 1e6), 'MB', flush=True); q -= 6
B = []
for f in sorted(os.listdir(ENT)):
    if f.startswith('bloque-') and f.endswith('.json'): B.append(json.load(open(os.path.join(ENT, f))))
json.dump({'niveles': cuenta, 'bloques': B, 'peso_mb': round(tam / 1e6)}, open(os.path.join(SAL, 'indice.json'), 'w'), ensure_ascii=False)
open(os.path.join(SAL, '.nojekyll'), 'w').close()
shutil.copyfile('pagina.html', os.path.join(SAL, 'index.html'))
print('listo:', cuenta, round(tam / 1e6), 'MB', flush=True)
