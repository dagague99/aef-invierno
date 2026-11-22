# ANÁLISIS EN FRÍO · Foto de invierno afinada, un bloque cada vez.
#
# Para un bloque de teselas (nivel 14, 1.024 puntos, unos 1,75 m por punto):
#   1. elige el mejor día de invierno de Sentinel-2 para ese sitio (sin nubes en el propio bloque, con nieve, sol alto);
#   2. baja las bandas de 10 m y la de onda corta (para saber dónde hay nieve aunque esté a la sombra);
#   3. las afina con el modelo abierto SEN2SR (Agencia Espacial Europea), de 10 m a 2,5 m;
#   4. sobre la nieve suma el relieve fino (Mapterhorn) iluminado con el sol de ese día, con las sombras que arroja el terreno;
#   5. donde no hay nieve suma el detalle de la foto aérea de verano (PNOA en España, IGN en Francia; en Andorra no hay);
#   6. saca las teselas en WebP.
# El color sale siempre de la imagen original de satélite: el detalle fino es del relieve y de la foto de verano.
#
# Uso: python bloque.py cobertura.json ID_DEL_BLOQUE carpeta_de_trabajo carpeta_de_salida
# Cada paso guarda lo que hace en la carpeta de trabajo: si se corta, al volver a lanzarlo sigue donde iba.

import io, json, math, os, sys, time, urllib.request, concurrent.futures as cf
import numpy as np, pyproj
from PIL import Image
from scipy import ndimage as ndi

COB, BID, TRAB, SAL = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
CAL = int(os.environ.get('CALIDAD', '82'))          # compresión de las teselas
HILOS = int(os.environ.get('HILOS', '6'))           # descargas a la vez (con mesura: son servicios públicos)
os.makedirs(TRAB, exist_ok=True); os.makedirs(SAL, exist_ok=True)
T15 = 512; MUNDO15 = 2 ** 15 * T15
UA = {'User-Agent': 'analisisenfrio.com (mapa de invierno; contacto en la web)'}
PC = 'https://planetarycomputer.microsoft.com/api'
def t0(): return time.time()
def log(*a): print(time.strftime('%H:%M:%S'), *a, flush=True)
def trae(u, intentos=5, espera=3):
    import urllib.error
    for i in range(intentos):
        try: return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=180).read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 404): return b''          # no hay pieza ahí: no se insiste
            err = e; time.sleep(espera * (i + 1))
        except Exception as e: err = e; time.sleep(espera * (i + 1))
    raise err
def trae_json(u, cuerpo=None):
    for i in range(5):
        try:
            rq = urllib.request.Request(u, data=json.dumps(cuerpo).encode() if cuerpo else None, headers=dict(UA, **({'Content-Type': 'application/json'} if cuerpo else {})))
            return json.load(urllib.request.urlopen(rq, timeout=120))
        except Exception as e: err = e; time.sleep(4 * (i + 1))
    raise err
def lon_de(x, z): return x / 2 ** z * 360 - 180
def lat_de(y, z): return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** z))))
def guarda(n, o): json.dump(o, open(os.path.join(TRAB, n), 'w'))
def hay(n): return os.path.exists(os.path.join(TRAB, n))
def ruta(n): return os.path.join(TRAB, n)

cob = json.load(open(COB)); BL = next(b for b in cob['bloques'] if b['id'] == BID); TES = [tuple(t) for t in BL['t']]
X14 = [t[0] for t in TES]; Y14 = [t[1] for t in TES]
# extensión del bloque en teselas de nivel 15
BX0, BX1, BY0, BY1 = 2 * min(X14), 2 * max(X14) + 1, 2 * min(Y14), 2 * max(Y14) + 1
LON0, LON1, LAT1, LAT0 = lon_de(BX0, 15), lon_de(BX1 + 1, 15), lat_de(BY0, 15), lat_de(BY1 + 1, 15)
log('bloque', BID, len(TES), 'teselas ·', round(LON0, 3), round(LAT0, 3), '→', round(LON1, 3), round(LAT1, 3))
MARG_DEM = 4                                           # teselas de nivel 15 de margen para el relieve (≈3,5 km: sombras largas)
DX0, DX1, DY0, DY1 = BX0 - MARG_DEM, BX1 + MARG_DEM, BY0 - MARG_DEM, BY1 + MARG_DEM

# ---------------------------------------------------------------- 1. el día
if not hay('escena.json'):
    m = 0.015; caja = [LON0 - m, LAT0 - m, LON1 + m, LAT1 + m]; C = []
    for a in range(2021, 2027):
        d = trae_json(PC + '/stac/v1/search', {'collections': ['sentinel-2-l2a'], 'bbox': caja, 'datetime': f'{a}-01-10T00:00:00Z/{a}-04-05T23:59:59Z', 'limit': 250, 'query': {'eo:cloud_cover': {'lt': 25}}})
        C += d['features']
    log('escenas candidatas', len(C))
    def clave(f): p = f['properties']; return p['datetime'][:10] + '/' + str(p.get('sat:relative_orbit', p.get('s2:datatake_id', '')))
    G = {}
    for f in C: G.setdefault(clave(f), []).append(f)
    # primera criba con lo que dice cada escena de sí misma
    def pre(fs):
        p = fs[0]['properties']; nub = min(x['properties'].get('eo:cloud_cover', 99) for x in fs)
        return nub * 2 - min(p.get('s2:snow_ice_percentage', 0), 60) * 0.3 - (90 - p.get('s2:mean_solar_zenith', 90)) * 0.4
    orden = sorted(G, key=lambda k: pre(G[k]))[:12]
    # segunda criba mirando el propio bloque: nubes, sombras de nube y huecos, con la capa de clasificación (60 m)
    tr = pyproj.Transformer.from_crs(4326, 3857, always_xy=True)
    mx0, my0 = tr.transform(caja[0], caja[1]); mx1, my1 = tr.transform(caja[2], caja[3])
    w = 260; h = int(w * (my1 - my0) / (mx1 - mx0))
    res = []
    for k in orden:
        cuenta = np.zeros((h, w), np.uint8); malo = np.zeros((h, w), bool); niev = np.zeros((h, w), bool); vacio = np.ones((h, w), bool)
        for f in G[k]:
            u = f"{PC}/data/v1/item/bbox/{mx0},{my0},{mx1},{my1}/{w}x{h}.tif?collection=sentinel-2-l2a&item={f['id']}&assets=SCL&coord_crs=epsg:3857&dst_crs=epsg:3857&resampling=nearest"
            try:
                import tifffile; a = tifffile.imread(io.BytesIO(trae(u)))
                a = a[..., 0] if a.ndim == 3 and a.shape[-1] <= 2 else (a[0] if a.ndim == 3 else a)
            except Exception: continue
            dentro = a > 0; v = vacio & dentro; vacio &= ~dentro
            malo |= v & np.isin(a, [1, 3, 8, 9, 10]); niev |= v & (a == 11)
        frac_malo = float(malo.mean() + vacio.mean()); frac_nieve = float(niev.mean())
        p = G[k][0]['properties']; sol = 90 - p.get('s2:mean_solar_zenith', 90)
        res.append((frac_malo * 300 - min(frac_nieve, 0.5) * 30 - sol * 0.35, k, frac_malo, frac_nieve, sol))
        log('  ', k, 'malo %.1f%%' % (frac_malo * 100), 'nieve %.0f%%' % (frac_nieve * 100), 'sol %.0f°' % sol)
    res.sort(); _, k, fm, fn, sol = res[0]
    fs = sorted(G[k], key=lambda f: f['properties'].get('eo:cloud_cover', 99)); p = fs[0]['properties']
    esc = {'clave': k, 'fecha': p['datetime'][:10], 'items': [f['id'] for f in fs], 'epsg': int(p.get('proj:epsg') or str(p.get('proj:code', 'EPSG:32631')).split(':')[-1]),
           'sol_rumbo': p.get('s2:mean_solar_azimuth'), 'sol_alt': 90 - p.get('s2:mean_solar_zenith'), 'malo': fm, 'nieve': fn}
    guarda('escena.json', esc); log('elegida', esc['fecha'], 'malo %.1f%%' % (fm * 100), 'nieve %.0f%%' % (fn * 100), 'sol %.0f°' % esc['sol_alt'], esc['items'])
ESC = json.load(open(ruta('escena.json')))
EPSG = ESC['epsg']; TRU = pyproj.Transformer.from_crs(4326, EPSG, always_xy=True)

# ---------------------------------------------------------------- 2. bandas de satélite en su malla original (10 m)
if not hay('s2.npy'):
    pts = [TRU.transform(lon_de(x, 15), lat_de(y, 15)) for x in (BX0, BX1 + 1) for y in (BY0, BY1 + 1)]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; mg = 800
    x0 = int(math.floor((min(xs) - mg) / 10) * 10); x1 = int(math.ceil((max(xs) + mg) / 10) * 10); y0 = int(math.floor((min(ys) - mg) / 10) * 10); y1 = int(math.ceil((max(ys) + mg) / 10) * 10)
    W = (x1 - x0) // 10; H = (y1 - y0) // 10; out = np.zeros((5, H, W), np.uint16)
    import tifffile
    def pieza(f, bandas, i, j, w, h, rs):
        bx0 = x0 + i * 10; bx1 = bx0 + w * 10; by1 = y1 - j * 10; by0 = by1 - h * 10
        u = f"{PC}/data/v1/item/bbox/{bx0},{by0},{bx1},{by1}/{w}x{h}.tif?collection=sentinel-2-l2a&item={f}" + ''.join('&assets=' + b for b in bandas) + f"&coord_crs=epsg:{EPSG}&dst_crs=epsg:{EPSG}&resampling={rs}"
        a = tifffile.imread(io.BytesIO(trae(u)))
        if a.ndim == 2: a = a[None]
        elif a.shape[-1] in (len(bandas), len(bandas) + 1) and a.shape[0] != len(bandas): a = np.moveaxis(a, -1, 0)
        return a[:len(bandas), :h, :w]
    P = 1000
    for f in ESC['items']:                                   # varias escenas del mismo paso: se rellena lo que falte
        for j in range(0, H, P):
            for i in range(0, W, P):
                w = min(P, W - i); h = min(P, H - j)
                if (out[0, j:j + h, i:i + w] > 0).all(): continue
                try: a = pieza(f, ['B04', 'B03', 'B02', 'B08'], i, j, w, h, 'nearest'); b = pieza(f, ['B11'], i, j, w, h, 'bilinear')
                except Exception as e: log('  pieza sin datos', f, i, j, str(e)[:60]); continue
                hueco = out[0, j:j + h, i:i + w] == 0
                for k in range(4): out[k, j:j + h, i:i + w][hueco] = a[k][hueco]
                out[4, j:j + h, i:i + w][hueco] = b[0][hueco]
    np.save(ruta('s2.npy'), out); guarda('malla.json', {'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'W': W, 'H': H})
    log('satélite', out.shape, 'sin datos %.2f%%' % (float((out[0] == 0).mean()) * 100))
MALLA = json.load(open(ruta('malla.json')))

# ---------------------------------------------------------------- 3. el modelo SEN2SR (10 m -> 2,5 m)
if not hay('sr.npy'):
    import torch, mlstac
    torch.set_num_threads(max(1, os.cpu_count() or 2))
    mdir = os.environ.get('MODELO', os.path.join(TRAB, '..', 'modelo'))
    if not os.path.exists(os.path.join(mdir, 'mlm.json')):
        mlstac.download(file="https://huggingface.co/tacofoundation/sen2sr/resolve/main/SEN2SRLite/NonReference_RGBN_x4/mlm.json", output_dir=mdir)
    model = mlstac.load(mdir).compiled_model(device=torch.device('cpu'))
    a = np.load(ruta('s2.npy'))[:4].astype('float32'); X = np.clip((a - 1000) / 10000.0, 0, None)
    C, H, W = X.shape; P = 128; S = 96; F = 4
    def ini(n):
        L = list(range(0, max(1, n - P + 1), S))
        if L[-1] != n - P: L.append(max(0, n - P))
        return L
    out = np.zeros((3, H * F, W * F), np.float32); peso = np.zeros((H * F, W * F), np.float32)
    r = np.minimum(np.arange(P * F) + 1, np.arange(P * F)[::-1] + 1).astype(np.float32); r = np.clip(r / (16 * F), 0, 1); win = np.outer(r, r)
    lote = []; ts = t0()
    def vuelca():
        global lote
        if not lote: return
        with torch.no_grad(): Y = model(torch.from_numpy(np.stack([X[:, j:j + P, i:i + P] for j, i in lote])).float()).numpy()
        for k, (j, i) in enumerate(lote): out[:, j * F:(j + P) * F, i * F:(i + P) * F] += Y[k, :3] * win; peso[j * F:(j + P) * F, i * F:(i + P) * F] += win
        lote = []
    for j in ini(H):
        for i in ini(W):
            lote.append((j, i))
            if len(lote) == 8: vuelca()
    vuelca(); out /= np.maximum(peso, 1e-6)
    np.save(ruta('sr.npy'), out.astype('float16')); log('modelo hecho en', round(t0() - ts), 's')

# ---------------------------------------------------------------- 4. relieve fino y foto de verano
os.makedirs(ruta('dem'), exist_ok=True); os.makedirs(ruta('foto'), exist_ok=True)
FPNOA = 'https://www.ign.es/wmts/pnoa-ma?layer=OI.OrthoimageCoverage&style=default&tilematrixset=GoogleMapsCompatible&Service=WMTS&Request=GetTile&Version=1.0.0&Format=image/jpeg&TileMatrix=16&TileCol={x}&TileRow={y}'
FIGN = 'https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM&TILEMATRIX=16&TILEROW={y}&TILECOL={x}&FORMAT=image/jpeg'
def baja_dem(t):
    x, y = t; f = ruta(f'dem/{x}_{y}.webp')
    if os.path.exists(f): return
    try: open(f, 'wb').write(trae(f'https://tiles.mapterhorn.com/15/{x}/{y}.webp'))
    except Exception: pass
def baja_foto(t):
    # Se guardan las dos fotos (España y Francia) cuando las hay: en la frontera cada una cubre su lado
    x, y = t
    for clave, u in (('p', FPNOA), ('i', FIGN)):
        f = ruta(f'foto/{x}_{y}.{clave}.jpg'); fv = f + '.no'
        if os.path.exists(f) or os.path.exists(fv): continue
        try:
            d = trae(u.format(x=x, y=y), intentos=3)
            if len(d) > 4000: open(f, 'wb').write(d); continue      # fuera de su país dan una pieza lisa y pequeña
        except Exception: pass
        open(fv, 'w').close()
if not hay('descargas.ok'):
    ts = t0()
    with cf.ThreadPoolExecutor(HILOS) as ex:
        list(ex.map(baja_dem, [(x, y) for x in range(DX0, DX1 + 1) for y in range(DY0, DY1 + 1)]))
        list(ex.map(baja_foto, [(x, y) for x in range(2 * BX0 - 1, 2 * BX1 + 3) for y in range(2 * BY0 - 1, 2 * BY1 + 3)]))
    open(ruta('descargas.ok'), 'w').close(); log('relieve y foto bajados en', round(t0() - ts), 's')

# ---------------------------------------------------------------- 5. sombras que arroja el relieve con el sol de ese día
def dem_tesela(x, y):
    f = ruta(f'dem/{x}_{y}.webp')
    if not os.path.exists(f): return None
    try: im = np.asarray(Image.open(f).convert('RGB')).astype(np.float32)
    except Exception: return None
    return im[..., 0] * 256 + im[..., 1] + im[..., 2] / 256 - 32768
if not hay('sombra.npy'):
    W = (DX1 - DX0 + 1) * 256; H = (DY1 - DY0 + 1) * 256; Zm = np.zeros((H, W), np.float32)
    for y in range(DY0, DY1 + 1):
        for x in range(DX0, DX1 + 1):
            e = dem_tesela(x, y)
            if e is not None: Zm[(y - DY0) * 256:(y - DY0 + 1) * 256, (x - DX0) * 256:(x - DX0 + 1) * 256] = e.reshape(256, 2, 256, 2).mean(axis=(1, 3))
    ps = 156543.03392 * math.cos(math.radians((LAT0 + LAT1) / 2)) / 2 ** 15
    az, el = math.radians(ESC['sol_rumbo']), math.radians(ESC['sol_alt']); ux, uy = math.sin(az), -math.cos(az)
    hmax = np.full_like(Zm, -1e9)
    for k in range(1, int(3200 / ps) + 1):
        ox, oy = int(round(k * ux)), int(round(k * uy)); caida = math.hypot(ox, oy) * ps * math.tan(el)
        a = Zm[max(0, oy):H + min(0, oy), max(0, ox):W + min(0, ox)] - caida
        v = hmax[max(0, -oy):H + min(0, -oy), max(0, -ox):W + min(0, -ox)]; np.maximum(v, a, out=v)
    np.save(ruta('sombra.npy'), (Zm - hmax).astype(np.float16)); log('sombras hechas, a la sombra %.0f%%' % (float((Zm - hmax < 0).mean()) * 100))

# ---------------------------------------------------------------- 6. la mezcla, tesela a tesela
P = dict(tono=2.35, k_rel=0.8, k_sombra=0.5, g_foto=1.0, sig=4.5, sig_foto=7.0, difusa=0.22, suav_rel=1.8, dx=3.5, dy=1.3, gama=0.88)
_c = {}
def datos():
    if not _c:
        _c['sr'] = np.load(ruta('sr.npy'), mmap_mode='r'); a = np.load(ruta('s2.npy')).astype(np.float32)
        _c['s2'] = np.clip((a[:4] - 1000) / 10000.0, 0, None); _c['b11'] = np.clip((a[4] - 1000) / 10000.0, 0, None); _c['vacio'] = a[0] == 0
        _c['mg'] = np.load(ruta('sombra.npy'), mmap_mode='r')
    return _c
def lonlat(px, py):
    return (px + 0.5) / MUNDO15 * 360 - 180, np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (py + 0.5) / MUNDO15))))
def utm_de(px0, py0, w, h, paso=32):
    gx = np.arange(0, w + paso, paso); gy = np.arange(0, h + paso, paso)
    lon, _ = lonlat(px0 + gx, 0); _, lat = lonlat(0, py0 + gy)
    LO, LA = np.meshgrid(lon, lat); E, N = TRU.transform(LO, LA); yy, xx = np.mgrid[0:h, 0:w].astype(np.float32) / paso
    return ndi.map_coordinates(E, [yy, xx], order=1), ndi.map_coordinates(N, [yy, xx], order=1)
def dem_area(px0, py0, w, h):
    tx0, ty0 = px0 // T15, py0 // T15; tx1, ty1 = (px0 + w - 1) // T15, (py0 + h - 1) // T15
    M = np.zeros(((ty1 - ty0 + 1) * T15, (tx1 - tx0 + 1) * T15), np.float32)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            e = dem_tesela(tx, ty)
            if e is not None: M[(ty - ty0) * T15:(ty - ty0 + 1) * T15, (tx - tx0) * T15:(tx - tx0 + 1) * T15] = e
    return M[py0 - ty0 * T15:py0 - ty0 * T15 + h, px0 - tx0 * T15:px0 - tx0 * T15 + w]
def foto_area(px0, py0, w, h):
    # Devuelve el brillo de la foto de verano y dos pesos: dónde vale la francesa (lo que no es blanco puro) y dónde la española
    t = 256; tx0, ty0 = px0 // t, py0 // t; tx1, ty1 = (px0 + w - 1) // t, (py0 + h - 1) // t
    sh = ((ty1 - ty0 + 1) * t, (tx1 - tx0 + 1) * t)
    Lp = np.zeros(sh, np.float32); Li = np.zeros(sh, np.float32); okp = np.zeros(sh, bool); oki = np.zeros(sh, bool)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            sl = (slice((ty - ty0) * t, (ty - ty0 + 1) * t), slice((tx - tx0) * t, (tx - tx0 + 1) * t))
            for clave, L, ok in (('p', Lp, okp), ('i', Li, oki)):
                f = ruta(f'foto/{tx}_{ty}.{clave}.jpg')
                if not os.path.exists(f): continue
                try: a = np.asarray(Image.open(f).convert('RGB')).astype(np.float32)
                except Exception: continue
                L[sl] = (0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]) / 255
                ok[sl] = (a.min(axis=2) < 250) if clave == 'i' else True
    oki = ndi.binary_erosion(oki, iterations=3)
    s = (slice(py0 - ty0 * t, py0 - ty0 * t + h), slice(px0 - tx0 * t, px0 - tx0 * t + w))
    wi = ndi.gaussian_filter(oki.astype(np.float32), 4)[s]; wp = ndi.gaussian_filter((okp & ~oki).astype(np.float32), 4)[s]
    return Lp[s], Li[s], wp, wi
def suave(x, a, b): t = np.clip((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t)
def tono(r): return (1 - np.exp(-np.clip(r, 0, None) * P['tono'])) ** P['gama']
def compone(x14, y14):
    c = datos(); MG = 64; px0 = 2 * x14 * T15 - MG; py0 = 2 * y14 * T15 - MG; w = h = 2 * T15 + 2 * MG
    E, N = utm_de(px0, py0, w, h); E = E + P['dx']; N = N - P['dy']
    col = (E - MALLA['x0']) / 2.5 - 0.5; fil = (MALLA['y1'] - N) / 2.5 - 0.5
    j0 = max(0, int(fil.min()) - 4); j1 = min(c['sr'].shape[1], int(fil.max()) + 5); i0 = max(0, int(col.min()) - 4); i1 = min(c['sr'].shape[2], int(col.max()) + 5)
    sr = np.asarray(c['sr'][:, j0:j1, i0:i1]).astype(np.float32)
    img = np.stack([ndi.map_coordinates(sr[k], [fil - j0, col - i0], order=3, mode='nearest') for k in range(3)])
    c10 = (E - MALLA['x0']) / 10 - 0.5; f10 = (MALLA['y1'] - N) / 10 - 0.5
    base = lambda a, o=1: ndi.map_coordinates(a, [f10, c10], order=o, mode='nearest')
    verde = base(c['s2'][1]); b11 = base(c['b11'])
    nieve = suave(ndi.gaussian_filter((verde - b11) / (verde + b11 + 1e-3), 2.5), 0.30, 0.55)
    plano = np.stack([base(c['s2'][k], 3) for k in range(3)])
    lum = lambda x: 0.30 * x[0] + 0.59 * x[1] + 0.11 * x[2]
    pl = np.stack([ndi.gaussian_filter(plano[k], 1.5) for k in range(3)]); img = pl * ((lum(img) + 0.004) / (lum(pl) + 0.004))[None]
    v = tono(img)
    z = ndi.gaussian_filter(dem_area(px0, py0, w, h), P['suav_rel'])
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32); fm = (py0 - DY0 * T15 + yy) / 2 - 0.25; cm = (px0 - DX0 * T15 + xx) / 2 - 0.25
    q0 = max(0, int(fm.min()) - 2); q1 = int(fm.max()) + 3; p0 = max(0, int(cm.min()) - 2); p1 = int(cm.max()) + 3
    lit = np.clip((ndi.map_coordinates(np.asarray(c['mg'][q0:q1, p0:p1]).astype(np.float32), [fm - q0, cm - p0], order=1, mode='nearest') + 3.0) / 6.0, 0, 1); lit = ndi.gaussian_filter(lit, 1.5)
    _, latf = lonlat(0, py0 + np.arange(h)); paso = (156543.03392 * np.cos(np.radians(latf)) / 2 ** 16).astype(np.float32)[:, None]
    dzdx = np.gradient(z, axis=1) / paso; dzdn = -np.gradient(z, axis=0) / paso
    az, el = math.radians(ESC['sol_rumbo']), math.radians(ESC['sol_alt']); s = (math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el))
    nn = np.sqrt(dzdx ** 2 + dzdn ** 2 + 1); cosi = np.clip((-dzdx * s[0] - dzdn * s[1] + s[2]) / nn, 0, None) / s[2]
    luz = (1 - P['difusa']) * cosi * lit + P['difusa']; r_sol = np.clip(luz / (ndi.gaussian_filter(luz, P['sig']) + 1e-3), 0.5, 1.45)
    cen = 1 / nn; r_som = np.clip((cen + 0.35) / (ndi.gaussian_filter(cen, P['sig']) + 0.35), 0.65, 1.4)
    sombra = 1 - ndi.gaussian_filter(lit * (cosi > 0.05), 2.0)
    f_rel = r_sol ** (P['k_rel'] * nieve) * r_som ** (P['k_sombra'] * nieve * sombra)
    Lp, Li, wp, wi = foto_area(px0, py0, w, h)
    det = lambda L: np.clip((L - ndi.gaussian_filter(L, P['sig_foto'])) / (ndi.gaussian_filter(L, P['sig_foto']) + 0.12), -0.6, 0.6)
    d = det(Lp) * wp + det(Li) * wi
    f_foto = np.clip(1 + P['g_foto'] * d * (1 - nieve), 0.62, 1.38)
    out = np.clip(v * (f_rel * f_foto)[None], 0, 1)[:, MG:-MG, MG:-MG]
    vac = base(c['vacio'].astype(np.float32), 0)[MG:-MG, MG:-MG] > 0.5          # donde el satélite no tenía datos: transparente
    rgb = (np.moveaxis(out, 0, -1) * 255 + 0.5).astype(np.uint8)
    if vac.any(): return Image.fromarray(np.dstack([rgb, np.where(vac, 0, 255).astype(np.uint8)]), 'RGBA')
    return Image.fromarray(rgb)
ts = t0(); n = 0
for x, y in TES:
    f = os.path.join(SAL, f'14-{x}-{y}.webp')
    if os.path.exists(f): continue
    compone(x, y).save(f, 'WEBP', quality=CAL, method=6); n += 1
    if n % 20 == 0: log('  teselas', n, 'de', len(TES), round(t0() - ts), 's')
json.dump({'id': BID, 'fecha': ESC['fecha'], 'items': ESC['items'], 'sol_alt': round(ESC['sol_alt'], 1), 'teselas': len(TES)}, open(os.path.join(SAL, f'bloque-{BID}.json'), 'w'))
log('bloque', BID, 'hecho:', n, 'teselas en', round(t0() - ts), 's ·', round(sum(os.path.getsize(os.path.join(SAL, q)) for q in os.listdir(SAL) if q.startswith('14-')) / 1e6, 1), 'MB en la salida')
