# dwg2c4d — da planimetria 2D (DWG/DXF) a modello 3D per Cinema 4D

Legge una pianta 2D, ne **estrude i muri**, **apre porte e finestre** (con architrave, parapetto e vetri),
aggiunge il **pavimento** e scrive un file **OBJ + MTL** pronto da importare in Cinema 4D, con un oggetto
per categoria (`Muri`, `Pilastri`, `Vetri`, `Pavimento`, `Soffitto`) e un materiale per ciascuno.

```
pianta.dwg  ──►  dwg2c4d  ──►  pianta.obj + pianta.mtl  ──►  Cinema 4D
```

## Installazione

Serve Python 3.10 o superiore.

```bash
pip install .            # dalla cartella del progetto
dwg2c4d --help
```

### Leggere i file `.dwg`

Il formato DWG è chiuso: lo strumento lo converte in DXF con uno di questi programmi gratuiti
(lo cerca da solo nei percorsi abituali, oppure indicalo con `--converter`):

| Programma | Note |
|---|---|
| [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter) | Consigliato. Windows, macOS, Linux. |
| [LibreDWG](https://www.gnu.org/software/libredwg/) (`dwg2dxf`) | Open source, da riga di comando. |

**Senza nessuno dei due** apri il DWG nel tuo CAD, scegli *Salva con nome → DXF* (versione 2013 o 2018)
e passa il `.dxf`: tutto il resto funziona uguale.

## Uso rapido

```bash
dwg2c4d pianta.dwg                       # crea pianta.obj accanto al file
dwg2c4d pianta.dwg -o 3d/casa.obj --altezza-muri 2.70
```

Alla fine stampa un riepilogo: **controllalo**. Le dimensioni (`Ingombro muri`) devono corrispondere a quelle
reali dell'edificio, e il numero di porte/finestre a quelle della pianta.

### Prova subito senza un tuo DWG

In `examples/` c'è una pianta di prova (`esempio.dxf`) e il risultato (`esempio.obj`). Per rigenerarla:

```bash
python examples/make_sample.py lines esempio.dxf     # stili: lines, jambs, polylines, hatch, centerline
dwg2c4d esempio.dxf
```

## Prima di convertire: i layer

Lo strumento capisce cosa è un muro dal **nome del layer**. Per vedere come vengono letti i tuoi:

```bash
dwg2c4d pianta.dwg --elenca-layer
```

```
LAYER     USO COME   CONTENUTO
FINESTRE  finestre   10 LINE
MURI      muri       32 LINE
PORTE     porte      2 INSERT  [blocchi: PORTA80, PORTA90]
QUOTE     -          1 LINE, 1 TEXT
```

Riconosce da solo i nomi italiani e inglesi più comuni (`MURI`, `PARETI`, `TRAMEZZI`, `A-WALL`, `PORTE`, `A-DOOR`,
`FINESTRE`, `SERRAMENTI`, `A-GLAZ`, `PILASTRI`, `A-COLS`…). `MURI_PORTANTI` è un layer di muri, non di porte.
Anche un blocco chiamato `PORTA90` o `FINESTRA120` viene riconosciuto, qualunque sia il suo layer.

Se un layer è classificato male, indicalo tu (nomi separati da virgola, `*` come jolly):

```bash
dwg2c4d pianta.dwg --muri "A-MURI*,TRAMEZZI" --porte "SERR_P" --finestre "SERR_F" --pilastri "STRUTT"
```

Tutto ciò che sta su altri layer (quote, testi, arredi, tratteggi) viene ignorato.
I layer spenti o congelati vengono saltati (`--includi-nascosti` per usarli).

## Come viene interpretato il disegno

**Muri** (`--modalita-muri`, default `auto`):

| Modalità | Il muro nel disegno è… |
|---|---|
| `solidi` | polilinee **chiuse**, campiture (HATCH) o SOLID. Un contorno interno a un altro è un buco: perimetro esterno + interno = anello di muro. |
| `doppia-linea` | due linee parallele (LINE, polilinee aperte, archi). Le estremità aperte, ad esempio dove c'è una porta, vengono chiuse da sole; i piccoli distacchi tra linee (qualche mm) vengono ricuciti. |
| `asse` | una sola linea sull'asse del muro: viene data uno spessore (`--spessore-muro`, default 0,30 m). |
| `auto` | combina: contorni chiusi → solidi; linee → doppia linea; se non si trova nessun muro a doppia linea, le linee diventano assi. Un contorno chiuso troppo largo per essere un muro (es. il perimetro di una stanza) è trattato come asse, con un avviso. |

**Porte e finestre**: ogni simbolo (blocco, oppure linee/archi sciolti vicini tra loro) viene cercato sul muro più vicino;
larghezza e posizione sono quelle del simbolo, lo spessore è quello del muro. Funziona sia se le linee del muro
**si interrompono** all'apertura, sia se il muro è **continuo** e il simbolo gli sta sopra: nel primo caso il muro
viene ricostruito sopra la porta (architrave) e sopra/sotto la finestra, poi tagliato solo nell'intervallo di quota giusto.

| Elemento | Quote predefinite (m) | Opzione |
|---|---|---|
| Altezza muri | 2,70 | `--altezza-muri` |
| Porta: altezza | 2,10 | `--altezza-porte` |
| Finestra: davanzale / altezza | 0,90 / 1,30 | `--davanzale`, `--altezza-finestre` |
| Pavimento: spessore (sotto quota 0) | 0,20 | `--spessore-pavimento`, `--no-pavimento` |
| Soffitto (solaio sopra i muri) | non creato | `--soffitto` |
| Vetri delle finestre (lastra da 2 cm) | creati | `--no-vetri` |

**Pilastri**: polilinee chiuse, campiture e cerchi sul layer dei pilastri, alti come i muri.

**Pavimento**: riempie il contorno degli edifici che racchiudono uno spazio (porte e varchi fino a ~1,2 m
vengono scavalcati).

**Più disegni nello stesso file** (pianta + sezioni + prospetti): usa `--area XMIN,YMIN,XMAX,YMAX`
(coordinate del disegno) per convertire solo la zona della pianta. Le entità che toccano l'area vengono
prese per intero, non tagliate.

## Importare in Cinema 4D

1. **File → Apri** (o *Unisci*) e scegli il `.obj`. Tieni `.mtl` nella stessa cartella: i materiali vengono creati.
2. Nella finestra delle opzioni d'importazione OBJ imposta **Unità = Metri** (scala 100%): il file è in metri.
   Se preferisci, genera direttamente in altre unità con `--unita-output cm` (o `mm`).
3. **Controlla l'orientamento**: in vista *Top* la pianta deve avere lo stesso verso del DWG. L'OBJ ha l'asse
   verticale Y e conserva il verso della pianta; Cinema 4D è un sistema sinistrorso e l'opzione d'importazione
   *Flip Z* ([documentazione Maxon](https://help.maxon.net/c4d/en-us/Content/html/FOBJIMPORT2-OBJIMPORTOPTIONS_GROUP_GEOMETRY.html))
   decide come viene convertito. Se la pianta ti appare **specchiata**, attiva/disattiva *Flip Z*
   oppure rigenera il file con `--specchia`.
4. Troverai gli oggetti `Muri`, `Pilastri`, `Vetri`, `Pavimento` (e `Soffitto`) già con un materiale base:
   sostituiscilo con i tuoi. Le normali sono già nel file.

Il modello ha una sola mesh per categoria, in modo da poter assegnare i materiali e creare le selezioni a mano.
La mesh non ha coordinate UV: usa una proiezione *Cubica* sul materiale.

## Opzioni

| Opzione | Significato |
|---|---|
| `-o`, `--output` | file `.obj` (default: accanto all'input) |
| `--elenca-layer` | mostra i layer e come sono classificati, poi esce |
| `--muri`, `--porte`, `--finestre`, `--pilastri` | layer per categoria (virgole, `*` jolly) |
| `--includi-nascosti` | usa anche i layer spenti/congelati |
| `--modalita-muri` | `auto`, `solidi`, `doppia-linea`, `asse` |
| `--spessore-muro` | spessore dei muri disegnati a linea singola (0,30) |
| `--spessore-max` | spessore massimo di un muro a doppia linea (0,60) |
| `--altezza-muri`, `--altezza-porte`, `--davanzale`, `--altezza-finestre` | quote in metri |
| `--no-pavimento`, `--spessore-pavimento`, `--soffitto`, `--no-vetri` | elementi aggiuntivi |
| `--unita` | unità del disegno: `mm`, `cm`, `m`, `in`, `ft` (default: lette dal file) |
| `--unita-output` | unità dell'OBJ: `m` (default), `cm`, `mm` |
| `--area` | converti solo questa zona del disegno |
| `--specchia` | specchia la pianta |
| `--converter` | percorso di `ODAFileConverter` o `dwg2dxf` |
| `--config` | file JSON con le impostazioni (le opzioni da riga di comando prevalgono) |

Esempio di file di configurazione (`casa.json`), con gli stessi nomi dei campi della classe `Config`:

```json
{
  "wall_height": 2.8,
  "wall_mode": "faces",
  "window_sill": 1.0,
  "layers": { "wall": ["A-MURI*"], "door": ["SERR_P"] }
}
```

## Cose da sapere (limiti)

- **Le unità**: se il file non le dichiara, vengono dedotte dalle dimensioni (con un avviso). È un'ipotesi:
  verifica `Ingombro muri` nel riepilogo e, se serve, forza con `--unita cm`.
- **Un piano alla volta.** Più piani sovrapposti o affiancati nello stesso file vanno separati con `--area`
  o su file distinti.
- Non vengono generati: scale, tetti, arredi, tratteggi, quote, testi. Le porte sono solo aperture (senza anta);
  le finestre hanno una lastra di vetro, senza telaio.
- Un muro a doppia linea con le estremità aperte viene chiuso in modo automatico solo se le due linee finiscono
  all'incirca allo stesso punto (entro `--spessore-max`). Disegni con linee molto sconnesse possono richiedere un
  po' di pulizia nel CAD (o la modalità `solidi`/`asse`).
- Le finestre devono stare su un layer proprio: se le linee del serramento sono sul layer dei muri, il muro viene
  spezzato lì e non si riconosce l'apertura.
- Gli spazi stretti (stanzini sotto i ~1 m di lato) possono essere scambiati per muro pieno in modalità
  `doppia-linea`; abbassa `--spessore-max` se succede.
- I blocchi (anche annidati) sono letti; i riferimenti esterni (xref) no: incorporali nel disegno prima di salvare.

## Risoluzione problemi

| Sintomo | Cosa fare |
|---|---|
| `Nessun muro riconosciuto` | `--elenca-layer`, poi indica il layer con `--muri`. |
| Modello 100× troppo grande o piccolo | Unità errate: `--unita`, oppure *Unità* nell'import di Cinema 4D. |
| Il muro è un blocco pieno senza stanze | I contorni sono assi, non solidi: `--modalita-muri asse`. |
| Mancano pezzi di muro | Linee non chiuse: prova `--modalita-muri doppia-linea` o `solidi`; `--spessore-max` più alto. |
| Una porta/finestra non compare | Il simbolo non tocca il muro (avviso nel riepilogo) o è su un layer non riconosciuto. |
| Pianta specchiata in Cinema 4D | `--specchia`, oppure *Flip Z* nelle opzioni d'importazione. |
| `Impossibile leggere il file DWG` | Installa ODA File Converter o LibreDWG, oppure salva il DWG come DXF. |

## Sviluppo

```bash
pip install -e ".[dev]"
pytest
```

I test costruiscono piante DXF sintetiche (`src/dwg2c4d/sample.py`) e verificano volumi, quote, orientamento degli
assi, coerenza tra normali e ordine dei vertici, tenuta delle mesh, rotazione della pianta, unità, e l'intera catena
da riga di comando, compresa la conversione DWG tramite un finto `dwg2dxf`.

Struttura: `dwgfile.py` (apertura DWG/DXF) → `reader.py` (entità → geometrie in metri) → `walls.py` e `openings.py`
(muri, porte, finestre) → `model.py` e `mesh.py` (estrusione) → `objwriter.py` (OBJ/MTL).
