"""
plan2c4d  -  importa in Cinema 4D il modello generato da plan2c4d.py

Come si usa:
  1. in Cinema 4D apri  Estensioni > Script Manager  (Extensions > Script Manager)
  2. File > Nuovo, incolla questo script (oppure File > Carica) e premi Esegui
  3. scegli il file  *_model.json  creato da plan2c4d.py

Il modello viene inserito nel documento attivo sotto un null con il nome della planimetria:
    Planimetria
      Murature / Pavimenti / Battiscopa / Infissi (un null per ogni F01, P01, ...)
Le mesh sono oggetti poligonali gia' pronti (niente Booleane, niente spline), con un
materiale per tipo di elemento (Muro, Pavimento, Telaio, Vetro, ...): cambiando quello
cambi tutti gli elementi dello stesso tipo.  Se Corona e' installato i materiali sono
Corona Physical, altrimenti materiali standard.
Unita': cm.  Assi: X = X del CAD, Z = Y del CAD, Y = quota (alto).

Versione di dwg2c4d: ogni infisso (F01, P01, ...) e' un null con l'ASSE al centro, nel punto piu' basso dell'infisso,
orientato sul muro: X lungo il muro, Y verso l'alto, Z verso l'esterno dell'edificio (per una porta interna: dal lato
in cui si apre l'anta). Le mesh dentro il null sono relative a quell'asse: per sostituire un infisso basta cancellarne
le mesh e mettere dentro il null il tuo modello (con l'asse al centro in basso) con posizione e rotazione a zero.
"""
import base64
import json
import math
import os
import zlib

import c4d
from c4d import gui

CORONA_PHYSICAL = 1056306          # id del Corona Physical Material
CORONA_BASE_COLOR = 20201
CORONA_ROUGHNESS = 20208
CORONA_PRESET = 20104              # menu "Apply preset"
CORONA_PRESET_GLASS_ARCH = 13
VIEWPORT_COLOR = 1041671

PACKED = None                      # (opzionale) modello compresso incorporato nello script


# ----------------------------------------------------------------------------------------
def load_model(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def unpack(packed):
    return json.loads(zlib.decompress(base64.b64decode(packed)).decode("utf-8"))


def make_material(doc, key, spec, use_corona=True):
    color = c4d.Vector(*spec["color"])
    mat = None
    if use_corona:
        try:
            mat = c4d.BaseMaterial(CORONA_PHYSICAL)
        except Exception:
            mat = None
    if mat is not None:
        mat.SetName(spec["name"])
        try:
            if key == "glass":
                mat[CORONA_PRESET] = CORONA_PRESET_GLASS_ARCH
            else:
                mat[CORONA_BASE_COLOR] = color
                mat[CORONA_ROUGHNESS] = float(spec.get("rough", 0.6))
            mat[VIEWPORT_COLOR] = color
        except Exception:
            pass
    else:
        mat = c4d.BaseMaterial(c4d.Mmaterial)
        mat.SetName(spec["name"])
        mat[c4d.MATERIAL_COLOR_COLOR] = color
        if key == "glass":
            mat[c4d.MATERIAL_USE_TRANSPARENCY] = True
            mat[c4d.MATERIAL_TRANSPARENCY_BRIGHTNESS] = 0.85
            mat[c4d.MATERIAL_TRANSPARENCY_REFRACTION] = 1.52
    doc.InsertMaterial(mat)
    return mat


def frame_matrix(g):
    """Matrice locale di un gruppo con 'origin', 'ex', 'ey' (cm, assi CAD): origine nel punto dato, X lungo 'ex',
    Y verso l'alto, Z lungo 'ey'.  (x, y, z) del CAD  ->  (x, z, y) di Cinema 4D."""
    ox, oy, oz = g["origin"]
    ex, ey = g["ex"], g["ey"]
    off = c4d.Vector(ox, oz, oy)
    v1 = c4d.Vector(ex[0], 0.0, ex[1])
    v2 = c4d.Vector(0.0, 1.0, 0.0)
    v3 = c4d.Vector(ey[0], 0.0, ey[1])
    return c4d.Matrix(off, v1, v2, v3)


def make_layer(doc, name, color):
    try:
        root = doc.GetLayerObjectRoot()
        lay = c4d.documents.LayerObject()
        lay.SetName(name)
        lay[c4d.ID_LAYER_COLOR] = c4d.Vector(*color)
        lay.InsertUnder(root)
        return lay
    except Exception:
        return None


def build(doc, model, use_corona=True, use_layers=True, phong_angle_deg=40.0):
    """Inserisce il modello nel documento.  Restituisce il null radice."""
    root = c4d.BaseObject(c4d.Onull)
    root.SetName(model["name"])
    doc.InsertObject(root)

    materials = {k: make_material(doc, k, v, use_corona) for k, v in model["materials"].items()}

    # gruppi (null) in ordine di dichiarazione: i genitori vengono sempre prima dei figli
    nulls = {model["name"]: root}
    last_child = {}                # per inserire in ordine: ultimo figlio inserito di ogni genitore
    layers = {}
    palette = {"Murature": (0.85, 0.85, 0.8), "Pavimenti": (0.6, 0.45, 0.3), "Battiscopa": (1, 1, 1),
               "Infissi": (0.3, 0.6, 0.9), "Soffitti": (0.9, 0.9, 0.9)}
    for g in model["groups"]:
        if g["name"] == model["name"]:
            continue
        n = c4d.BaseObject(c4d.Onull)
        n.SetName(g.get("label") or g["name"])
        par = nulls.get(g["parent"], root)
        doc.InsertObject(n, par, last_child.get(id(par)))
        last_child[id(par)] = n
        nulls[g["name"]] = n
        if g.get("origin"):          # asse del gruppo (infissi): le mesh dei figli sono relative ad esso
            n.SetMl(frame_matrix(g))
        if use_layers and g["parent"] == model["name"] and g["name"] in palette:
            lay = make_layer(doc, g["name"], palette[g["name"]])
            if lay is not None:
                layers[g["name"]] = lay
                n[c4d.ID_LAYER_LINK] = lay

    phong = math.radians(phong_angle_deg)
    total_p = total_f = 0
    for ob in model["objects"]:
        pts = ob["points"]
        polys = ob["polys"]
        npts, npol = len(pts) // 3, len(polys) // 4
        po = c4d.PolygonObject(npts, npol)
        po.SetName(ob["name"])
        # CAD (x, y, z-alto)  ->  C4D (x, y-alto, z):  (x, z, y)
        po.SetAllPoints([c4d.Vector(pts[3 * i], pts[3 * i + 2], pts[3 * i + 1]) for i in range(npts)])
        for i in range(npol):
            a, b, c, d = polys[4 * i: 4 * i + 4]
            # il passaggio destrorso -> sinistrorso inverte il verso delle facce: si riordinano i vertici
            if c == d:
                po.SetPolygon(i, c4d.CPolygon(a, c, b))
            else:
                po.SetPolygon(i, c4d.CPolygon(a, d, c, b))
        po.Message(c4d.MSG_UPDATE)
        tp = po.MakeTag(c4d.Tphong)
        tp[c4d.PHONGTAG_PHONG_ANGLELIMIT] = True
        tp[c4d.PHONGTAG_PHONG_ANGLE] = phong
        mat = materials.get(ob["material"])
        if mat is not None:
            tt = po.MakeTag(c4d.Ttexture)
            tt[c4d.TEXTURETAG_MATERIAL] = mat
            tt[c4d.TEXTURETAG_PROJECTION] = c4d.TEXTURETAG_PROJECTION_CUBIC
        parent = nulls.get(ob["group"], root)
        doc.InsertObject(po, parent, last_child.get(id(parent)))
        last_child[id(parent)] = po
        total_p += npts
        total_f += npol
    c4d.EventAdd()
    return root, total_p, total_f


def main():
    doc = c4d.documents.GetActiveDocument()
    model = unpack(PACKED) if PACKED else None
    if model is None:
        path = c4d.storage.LoadDialog(c4d.FILESELECTTYPE_ANYTHING, "Scegli il file *_model.json")
        if not path:
            return
        model = load_model(path)
    doc.StartUndo()
    root, npts, npol = build(doc, model)
    doc.AddUndo(c4d.UNDOTYPE_NEW, root)
    doc.EndUndo()
    gui.StatusSetText("plan2c4d: %d punti, %d poligoni importati" % (npts, npol))


if __name__ == "__main__":
    main()
