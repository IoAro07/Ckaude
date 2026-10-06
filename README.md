# dwg2c4d — da planimetria 2D (DWG/DXF) a modello 3D per Cinema 4D

Legge una pianta 2D, ne **estrude i muri**, **apre porte e finestre** (con architrave, parapetto e vetri),
aggiunge il **pavimento** e scrive un file **OBJ + MTL** pronto da importare in Cinema 4D, con un oggetto
per categoria (`Muri`, `Pilastri`, `Vetri`, `Pavimento`, `Soffitto`, `Tetto`) e un materiale per ciascuno.

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

Provato con LibreDWG 0.13.3 su un DWG AutoCAD 2018 reale (formato `AC1032`): si converte in meno di un secondo.
LibreDWG può lasciare senza definizione qualche blocco anonimo (`*U`, `*X`): lo strumento lo salta e te lo segnala.

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

## Il flusso tipico con un DWG reale

I disegni veri non hanno quasi mai un layer chiamato "MURI" e contengono spesso più cose insieme (pianta,
prospetti, tetto, verde). Il metodo che funziona:

```bash
dwg2c4d pianta.dwg --elenca-layer
```

1. Guarda le **unità dichiarate** in testa all'elenco: se le misure non tornano (es. dichiara `mm` ma la porta è larga 90),
   forzale con `--unita cm`.
2. Trova il **layer dei muri** (spesso `0` o uno con le linee dei muri a doppia linea) e la **zona della pianta**
   (la riga `zona:` sotto ogni layer dà le coordinate): serve a escludere prospetti e sezioni.
3. Converti:

```bash
dwg2c4d pianta.dwg -o casa.obj --muri "0" --unita cm --area 72400,48300,74600,49350
```

4. Leggi il riepilogo e gli avvisi, importa in Cinema 4D.

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

I layer con nomi come `Prospetto…`, `Sezione…`, `Arredo…`, `Quote…`, `Testi`, `Tetto`, `Verde` non vengono mai presi per muri,
porte o finestre, anche se contengono parole come "parete" o "porta" (es. il blocco `Porta Asciugamani`).

I **blocchi inseriti su un layer di muri** (arredi, sanitari: capita molto sul layer `0`) vengono ignorati e contati
in un avviso; se invece contengono davvero muri usa `--muri-da-blocchi`.

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

## Quote delle aperture e tetto dai prospetti (facoltativo)

Di norma altezze di porte e finestre sono i valori predefiniti e il tetto non viene creato. Se nel file ci sono i
**prospetti** e la **pianta del tetto** puoi usarli.

**Altezze di porte e finestre dal prospetto**: `--prospetto XMIN,YMIN,XMAX,YMAX[,QUOTA_Y]`, uno per facciata
(ripetibile). Condizioni:

- il prospetto deve essere **in proiezione sulla pianta**: sotto di essa (facciata sud) o sopra (facciata nord), con
  le **stesse coordinate X**. Se è disposto altrove in tavola non funziona (lo strumento lo segnala);
- le porte e le finestre del prospetto devono essere riconoscibili (layer `Porte`/`Finestre` o blocchi chiamati
  `Porta…`/`Finestra…`, anche su un layer `Prospetto…`);
- la **quota zero** (pavimento finito) è il fondo della porta più bassa del prospetto; se non c'è una porta, o se
  vuoi forzarla, indica come quinto valore la Y del pavimento finito.

Ogni simbolo del prospetto viene associato all'apertura della pianta con lo stesso intervallo di X (la più esterna);
un simbolo disegnato con telaio e vetro annidati conta come uno solo. Le aperture senza corrispondenza restano ai
valori predefiniti, e il riepilogo dice quante sono state lette (`Prospetto sud: 2 di 2 aperture…`).

**Tetto**: `--tetto`. Si costruisce dalle linee del layer `Tetto`/`Roof`/`Copertura` (o `--layer-tetto`):
contorno, colmi, displuvi e compluvi, disegnati sul piano del tetto.

- le linee formano le falde; ogni falda sale dalla propria gronda con una pendenza;
- **pendenza**: `--pendenza GRADI`; altrimenti viene **dedotta dal prospetto**, se le linee orizzontali dei colmi
  (stessa estensione in X dei colmi della pianta del tetto) sono presenti, e ogni falda prende la sua; altrimenti
  25 gradi. Con più prospetti si usano tutti;
- la pianta del tetto è **centrata sui muri**; se è disegnata altrove nel file indica `--area-tetto`, e se serve
  correggerne la posizione `--sposta-tetto DX,DY` (unità del disegno);
- il tetto è un solido chiuso, spesso `--spessore-tetto` (0,15 m), con la gronda alla quota dei muri.

Nella pianta del tetto vanno solo contorno, colmi e displuvi: altre linee (travi, griglie, quote su quel layer)
spezzano le falde. Se ci sono due contorni annidati (gronda e linea del muro) il tetto si ferma al contorno interno.
Con il solo contorno viene una falda unica, con un avviso. Un tetto disegnato solo come campitura usa il suo bordo.

```bash
dwg2c4d pianta.dwg -o casa.obj --muri "0" --unita cm --area=72400,48300,74600,49350 \
    --prospetto=72400,46900,75150,47470 --tetto --area-tetto=72400,49700,74100,50750
```

> Attenzione: se il primo numero di un valore è negativo scrivi `--area=-200,-200,…` con il segno uguale
> (altrimenti la riga di comando lo scambia per un'opzione).

## Importare in Cinema 4D

Ci sono due modi. Il secondo è consigliato se usi Corona.

**A. File OBJ** (descritto qui sotto): semplice, ma devi impostare l'importazione (unità, *Flip Z*) e i materiali
sono quelli base del file `.mtl`.

**B. File `_model.json` + script Python in Cinema 4D** (`--json-c4d`): lo script crea oggetti nativi, raggruppati
per tipo (`Murature`, `Infissi`, `Pavimenti`, `Tetto`…), con un materiale per tipo (Corona Physical se Corona è
installato, altrimenti standard), e risolve da solo assi e orientamento delle facce. Il file è in **centimetri**,
con assi del disegno (X, Y, Z in alto). Il formato è quello letto dallo script `plan2c4d_import.py` (Script
Manager di Cinema 4D → scegli il `*_model.json`). Lo strumento ha controllato che quello script accetti il file con un
modulo `c4d` simulato; **non è stato provato dentro Cinema 4D** e gli ID dei parametri Corona dello script dipendono
dalla tua versione di Corona.

**Origine del modello** (`--origine`): i disegni reali stanno spesso a centinaia di metri dall'origine (il tuo
a circa 730 m). Di default il modello viene portato al **centro dei muri** (`--origine centro`), con la quota
del pavimento a zero; `minimo` mette lo zero nell'angolo, `disegno` lascia le coordinate del CAD. Il riepilogo
indica di quanto è stato spostato.

1. **File → Apri** (o *Unisci*) e scegli il `.obj`. Tieni `.mtl` nella stessa cartella: i materiali vengono creati.
2. Nella finestra delle opzioni d'importazione OBJ imposta **Unità = Metri** (scala 100%): il file è in metri.
   Se preferisci, genera direttamente in altre unità con `--unita-output cm` (o `mm`).
3. **Controlla l'orientamento**: in vista *Top* la pianta deve avere lo stesso verso del DWG. L'OBJ ha l'asse
   verticale Y e conserva il verso della pianta; Cinema 4D è un sistema sinistrorso e l'opzione d'importazione
   *Flip Z* ([documentazione Maxon](https://help.maxon.net/c4d/en-us/Content/html/FOBJIMPORT2-OBJIMPORTOPTIONS_GROUP_GEOMETRY.html))
   decide come viene convertito. Se la pianta ti appare **specchiata**, attiva/disattiva *Flip Z*
   oppure rigenera il file con `--specchia`.
4. Troverai gli oggetti `Muri`, `Pilastri`, `Vetri`, `Pavimento` (e `Soffitto`, `Tetto`) già con un materiale base:
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
| `--muri-da-blocchi` | leggi come muri anche i blocchi inseriti su un layer di muri |
| `--modalita-muri` | `auto`, `solidi`, `doppia-linea`, `asse` |
| `--spessore-muro` | spessore dei muri disegnati a linea singola (0,30) |
| `--spessore-max` | spessore massimo di un muro a doppia linea (0,60) |
| `--altezza-muri`, `--altezza-porte`, `--davanzale`, `--altezza-finestre` | quote in metri |
| `--no-pavimento`, `--spessore-pavimento`, `--soffitto`, `--no-vetri` | elementi aggiuntivi |
| `--unita` | unità del disegno: `mm`, `cm`, `m`, `in`, `ft` (default: lette dal file) |
| `--unita-output` | unità dell'OBJ: `m` (default), `cm`, `mm` |
| `--area` | converti solo questa zona del disegno |
| `--prospetto` | zona di un prospetto (ripetibile): altezze di porte e finestre; 5° valore opzionale = Y del pavimento finito |
| `--tetto`, `--layer-tetto`, `--area-tetto` | costruisci il tetto dalla pianta del tetto |
| `--pendenza`, `--spessore-tetto`, `--sposta-tetto` | pendenza (gradi), spessore (0,15 m), spostamento della pianta del tetto |
| `--origine` | `centro` (default), `minimo`, `disegno`: dove sta lo zero del modello |
| `--json-c4d` | scrivi anche `NOME_model.json` per lo script di importazione di Cinema 4D |
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
  verifica `Ingombro muri` nel riepilogo e, se serve, forza con `--unita cm`. Se le unità dichiarate sono sbagliate
  (succede: un file in cm che dichiara mm), lo strumento avvisa quando l'edificio risulta più piccolo di 3 m o più
  grande di 300 m.
- **Un piano alla volta.** Più piani sovrapposti o affiancati nello stesso file vanno separati con `--area`
  o su file distinti.
- Non vengono generati: scale, arredi, tratteggi, quote, testi, comignoli e abbaini. Le porte sono solo aperture
  (senza anta); le finestre hanno una lastra di vetro, senza telaio.
- **Il tetto** nasce dalle linee della pianta del tetto: non c'è nessun calcolo statico né ricostruzione da zero.
  Gronda sempre alla quota dei muri, senza sporgenza oltre il contorno interno; falde con pendenze diverse sono
  gestite solo se i colmi nel prospetto le fissano. Se la pianta del tetto è confusa il risultato lo sarà.
- **I prospetti** servono solo per le facciate nord e sud in proiezione; est e ovest, o prospetti ruotati o
  disposti altrove in tavola, non sono letti.
- Gli elementi **non muro** disegnati su un layer che usi per i muri (bordi di piscine, muretti, pavimentazioni)
  diventano muri: scegli bene i layer o ritaglia con `--area`.
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
| `--area -200,...` dà errore | Con numeri negativi scrivi `--area=-200,...` (con il segno uguale). |
| Il tetto è piatto o strano | Controlla il layer del tetto (`--layer-tetto`), che contenga solo contorno e colmi, e `--area-tetto`; leggi gli avvisi `Tetto:`. |
| Le finestre non prendono le quote del prospetto | Il prospetto deve avere le stesse X della pianta e stare sotto/sopra; guarda l'avviso e `Prospetto sud: N di M`. |
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
