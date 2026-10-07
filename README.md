# dwg2c4d — da planimetria 2D (DWG/DXF) a modello 3D per Cinema 4D

Legge una pianta 2D, ne **estrude i muri** (con i tramezzi a parte), **apre porte, finestre e vani** con le
quote che trova (scritte del disegno, prospetti), costruisce **infissi dettagliati** (imbotto, cornice, telai, ante,
maniglie, toppe), **pavimenti per locale**, **battiscopa** e, se vuoi, il **tetto**. Scrive un file **OBJ + MTL**
per Cinema 4D, e se vuoi anche un `*_model.json` per lo script di importazione (oggetti nativi, materiali Corona).

```
pianta.dwg  ──►  dwg2c4d  ──►  pianta.obj + .mtl  (+ _model.json)  ──►  Cinema 4D
                           └►  pianta_aperture.csv   (porte/finestre: correggile in Excel e rilancia)
                           └►  pianta_controllo_pianta.png, pianta_anteprima_3d.png, pianta_report.txt
```

## Installazione

### 1. Avere il progetto in locale

Il codice sta nel repository pubblico <https://github.com/IoAro07/Ckaude> (il ramo predefinito è
`claude/dwg-to-3d-cinema4d-0c0if5`: non serve alcun login). Due modi:

- **Scaricarlo come ZIP**: nella pagina del repository *Code → Download ZIP*, poi decomprimi in una cartella
  qualsiasi (per esempio `C:\dwg2c4d`).
- **Con git**: `git clone https://github.com/IoAro07/Ckaude.git`

In alternativa il file `dwg2c4d-0.1.0-py3-none-any.whl` (se te l'hanno consegnato): `pip install
dwg2c4d-0.1.0-py3-none-any.whl` installa tutto senza scaricare il progetto, ma non include gli script `.bat` né lo
script di Cinema 4D, che stanno nella cartella del progetto.

### 2. Python e le librerie (una volta sola)

1. Serve **Python 3.10 o superiore**: da [python.org](https://www.python.org/downloads/) (Windows: spunta
   *Add Python to PATH*; macOS/Linux di solito c'è già, controlla con `python3 --version`).
2. **Windows**: doppio clic su `installa.bat` nella cartella del progetto. **Altrove** (o se preferisci il terminale),
   dalla cartella del progetto:

```bash
pip install .
dwg2c4d --help           # se il comando non si trova: python -m dwg2c4d --help
```

Installa da solo `ezdxf`, `shapely`, `numpy` e `matplotlib` (quest'ultimo serve solo alla pianta di controllo).

Su Windows pip può installare "per l'utente" (avviso *Defaulting to user installation*): è normale, ma il comando
`dwg2c4d` potrebbe non essere nel PATH. I file `.bat` usano `python -m dwg2c4d`, che funziona sempre; da terminale
scrivi `python -m dwg2c4d pianta.dwg` al posto di `dwg2c4d pianta.dwg`.

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

**Windows, senza terminale**: trascina il `.dwg` (o `.dxf`) su `converti.bat`; `elenca_layer.bat` mostra come
vengono letti i layer. **Da terminale** (ovunque):

```bash
dwg2c4d pianta.dwg                       # crea pianta.obj accanto al file, più tabella, immagini e report
dwg2c4d pianta.dwg --json-c4d            # in più pianta_model.json per lo script di Cinema 4D
dwg2c4d pianta.dwg -o 3d/casa.obj --altezza-muri 2.70
```

Alla fine stampa un riepilogo: **controllalo**, e guarda `pianta_controllo_pianta.png` (muri, locali e ogni
apertura con sigla e misure) e `pianta_report.txt`. Le dimensioni (`Ingombro muri`) devono corrispondere a quelle
reali dell'edificio, e il numero di porte/finestre a quelle della pianta.

Se il disegno ha più cose sullo stesso layer (il tetto o una sezione disegnati accanto alla pianta) il riepilogo lo
dice (`I muri formano 2 gruppi distanti`) e ti dà l'`--area` di ciascun gruppo da copiare.

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
Se porte e finestre stanno **sullo stesso layer** (`Infissi`, `Serramenti`) le distingue dalla forma: un simbolo con
l'**arco di rotazione** (quarto di cerchio) è una porta, senza arco è una finestra. Lo dice nella nota dell'apertura
(e nella tabella, `origine_misure`): se sbaglia, `MODIFICA_tipo`. Un layer chiamato `PORTE` o `FINESTRE` non viene mai
rimesso in discussione.
Anche un blocco chiamato `PORTA90` o `FINESTRA120` viene riconosciuto, qualunque sia il suo layer.

I layer con nomi come `Prospetto…`, `Sezione…`, `Arredo…`, `Quote…`, `Testi`, `Tetto`, `Verde` non vengono mai presi per muri,
porte o finestre, anche se contengono parole come "parete" o "porta" (es. il blocco `Porta Asciugamani`).

I **blocchi inseriti su un layer di muri** (arredi, sanitari: capita molto sul layer `0`) vengono ignorati e contati
in un avviso; se invece contengono davvero muri usa `--muri-da-blocchi`.

Se un layer è classificato male, indicalo tu (nomi separati da virgola, `*` come jolly):

```bash
dwg2c4d pianta.dwg --muri "A-MURI*,TRAMEZZI" --porte "SERR_P" --finestre "SERR_F" --pilastri "STRUTT"
```

**Più piani.** Se i layer portano il numero del piano nel nome (`P1_Muri`, `P1_Infissi`, `pianta2 muri`, `Piano 3`...)
si legge **un piano alla volta**: di default il più basso (e lo dice), `--piano 2` sceglie un altro e il file si chiama
`NOME_p2.obj`. I layer senza numero (prospetti, testi, tetto...) valgono per tutti. `--elenca-layer` elenca i piani
trovati. I piani non vengono impilati in un unico modello: ognuno è un OBJ.

**Layer con nomi che il programma non conosce.** `--elenca-layer` finisce con le **PROPOSTE**, ciascuna con la sua
confidenza e il perché: i nomi tipici dicono subito arredi, quote/testi, retini, verde, prospetti, impianti; per le
linee il programma **prova davvero** l'ipotesi: un layer che costruito come muri chiude dei locali è probabilmente il
layer dei muri (confidenza alta con 2 o più locali), un layer i cui simboli stanno sui muri e hanno l'**arco di
rotazione** sono le porte. Un solo candidato di muri è "il" candidato; gli altri restano a confidenza bassa.
Le proposte **non vengono applicate da sole**: con `--accetta-proposte` si usano quelle a confidenza media o alta,
solo per le categorie che il nome del layer o le tue opzioni non hanno già deciso (il programma lo dice a video).

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

**Vani senza simbolo**: due testate di muro libere, una di fronte all'altra e allineate, con un vuoto tra 0,5 m e
`--vano-max` (default 2 m) e senza nessuna porta/finestra in mezzo, sono un passaggio disegnato solo come interruzione
del muro. Diventa un'apertura `vano` (`V01`...): architrave sopra i 2,10 m (o l'altezza scritta), imbotto e cornice,
nessuna anta. Compare nella tabella: `MODIFICA_tieni = no` lo richiude con il muro; `--no-vani` non li cerca.

**Pavimenti, battiscopa, tramezzi** (convenzione dei nomi dei layer, anche in inglese: floor, skirting):

| Layer (nome contiene) | Cosa produce |
|---|---|
| `Pavimenti`, `floor`, `solaio` | poligoni chiusi: **un oggetto per poligono** (`Pavimento_Soggiorno`...), col nome del locale scritto sopra; se un poligono ne sta dentro un altro viene scavato (altra finitura) |
| nessun layer di pavimenti | un oggetto per **locale chiuso dai muri**, esteso sotto le porte (nessun buco alla soglia); con un solo locale senza nome resta la lastra unica `Pavimento`. `--no-pavimento-per-locale` non separa |
| `Battiscopa`, `skirting`, `zoccolino` | linee/polilinee: fascetta alta 8 cm, spessa 1,2 cm (`skirting_height`, `skirting_thickness` nel file di configurazione), dalla parte della stanza se la linea corre sulla faccia del muro; si ferma alle porte |
| `Fondelli`, `Tramezzi`, `Divisori` | sono muri come gli altri, ma finiscono nell'oggetto **`Tramezzi`** (materiale a parte); `--no-tramezzi` li lascia in `Muri` |

**Pilastri**: polilinee chiuse, campiture e cerchi sul layer dei pilastri, alti come i muri.

**Pavimento**: riempie il contorno degli edifici che racchiudono uno spazio (porte e varchi fino a ~1,2 m
vengono scavalcati).

**Più disegni nello stesso file** (pianta + sezioni + prospetti): usa `--area XMIN,YMIN,XMAX,YMAX`
(coordinate del disegno) per convertire solo la zona della pianta. Le entità che toccano l'area vengono
prese per intero, non tagliate.

## Quote delle aperture e tetto dai prospetti (facoltativo)

Di norma altezze di porte e finestre sono i valori predefiniti e il tetto non viene creato. Se nel file ci sono i
**prospetti** e la **pianta del tetto** puoi usarli.

**Altezze di porte e finestre dal prospetto**: se nel file c'è un layer chiamato `Prospetto…` (`Prospetto Frontale`,
`prospetto1`…) il prospetto viene **trovato da solo**: vale se sta tutto sotto o sopra la pianta, nella sua stessa
fascia di X, e contiene porte o finestre (un prospetto interno, di una parete di cucina, non ne ha e viene lasciato).
Il riepilogo dice dove l'ha trovato (`trovato da solo sul layer…`); `--no-prospetti-auto` lo spegne.
Per indicare tu la zona: `--prospetto XMIN,YMIN,XMAX,YMAX[,QUOTA_Y]`, uno per facciata (ripetibile); così la ricerca
automatica non parte. Condizioni:

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
- ogni falda è un **piano proprio** (dalla sua gronda e dalla sua pendenza). Dove una falda confina con un'altra a
  quota diversa (il timpano di un volume alto che sorge sopra una falda bassa) si aggiunge la **parete verticale**
  del gradino; il colmo di un volume vale solo per le sue falde, non per chi lo sfiora;
- il tetto è un **solido pieno**: superficie inclinata, fondo piatto `--spessore-tetto` (0,15 m) sotto la gronda,
  lati verticali. La gronda è alla quota dei muri.

Nella pianta del tetto vanno solo contorno, colmi e displuvi: altre linee (travi, griglie, quote su quel layer)
spezzano le falde. Se ci sono due contorni annidati (gronda e linea del muro) il tetto si ferma al contorno interno.
Con il solo contorno viene una falda unica, con un avviso. Un tetto disegnato solo come campitura usa il suo bordo.

```bash
dwg2c4d pianta.dwg -o casa.obj --muri "0" --unita cm --area=72400,48300,74600,49350 \
    --prospetto=72400,46900,75150,47470 --tetto --area-tetto=72400,49700,74100,50750
```

> Attenzione: se il primo numero di un valore è negativo scrivi `--area=-200,-200,…` con il segno uguale
> (altrimenti la riga di comando lo scambia per un'opzione).

## Infissi dettagliati e tabella delle aperture

Di default (`--infissi dettagliati`) ogni apertura diventa un oggetto riconoscibile, fatto di scatole orientate
(niente booleane), in quattro gruppi: `Telai`, `Ante`, `Vetri`, `Maniglie`.

* **Porte**: imbotto (spallette e architrave), cornice sui due lati del muro, un'anta per ogni **arco di
  rotazione** del simbolo in pianta. Il centro dell'arco è la **cerniera**: la maniglia (con placca e **toppa**)
  sta sul lato opposto, con la leva rivolta verso la cerniera. Porta doppia = due archi = due ante.
  Senza arco la cerniera non si deduce: l'anta è incernierata a sinistra e la nota lo dice.
* **Finestre e finestroni**: telaio fisso, un'anta per ogni spazio tra le **linee che tagliano il simbolo**
  di traverso al muro (una linea = 2 ante, due = 3...; le doppie linee vicine contano una volta), un vetro e una
  maniglia per ogni anta, davanzale e cornice. Una porta-finestra (davanzale 0) è la stessa cosa senza soglia.
* `--infissi semplici` dà solo la lastra di vetro (modello più leggero).
* Da riga di comando ogni apertura è un gruppo a sé (`Infissi` > `F01` > `Telai_F01`, `Vetri_F01`, `Maniglie_F01`;
  `Infissi` > `P01` > `Telai_P01`, `Ante_P01`...): puoi spostarla o cambiarne il materiale da sola. I materiali
  restano uno per tipo (Telai, Ante, Vetri, Maniglie). `--infissi-uniti` riunisce le parti di tutte le aperture
  in pochi oggetti.

Ogni conversione scrive anche **`NOME_aperture.csv`** (separatore `;`, centimetri, virgola decimale: si apre
con Excel): una riga per apertura, con sigla (`F01`, `P01`... in ordine di lettura, dall'alto a sinistra), tipo,
posizione, larghezza, spessore del muro, davanzale, altezza, numero di ante, cerniera e **da dove viene ogni
misura** (simbolo, prospetto, predefinita...).

Per correggere qualcosa non serve toccare il disegno: compila le colonne `MODIFICA_*` (solo le celle compilate
valgono) e rilancia con la tabella:

```bash
dwg2c4d pianta.dwg --tabella pianta_aperture.csv
```

| Colonna | Valori |
|---|---|
| `MODIFICA_tipo` | `finestra`, `porta`, `vano` |
| `MODIFICA_larghezza`, `MODIFICA_davanzale`, `MODIFICA_altezza` | centimetri |
| `MODIFICA_ante` | da 1 a 8 |
| `MODIFICA_cerniera` | `sinistra`, `destra`, `ovest`/`est` (muro lungo X), `sud`/`nord` (muro lungo Y), `doppia` |
| `MODIFICA_tieni` | `no` = l'apertura non esiste: il vano viene chiuso con il muro |

Le sigle seguono l'ordine di lettura: se cambi le opzioni (area, layer) possono cambiare. Una riga con sigla
inesistente o con un valore non valido viene segnalata e ignorata; il file in ingresso non viene mai
sovrascritto (la nuova tabella si chiama `NOME_aperture_nuova.csv` se il nome coincide). `--no-tabella` non la scrive.
Ordine di priorità per le quote: **tabella > scritta > prospetto > valori predefiniti** (la posizione e la
larghezza restano quelle del simbolo disegnato).

## Scritte del disegno: quote, nomi dei locali, altezze

I testi (TEXT, MTEXT, attributi dei blocchi, multileader) vengono letti e collegati alla pianta; `--no-scritte` li ignora.

| Scritta | Cosa ne ricavo |
|---|---|
| `120x150`, `120 x 150`, `L120 H150`, oppure due righe `120` sopra `150` | **larghezza x altezza** dell'apertura più vicina |
| `120x150x90` | come sopra, il terzo numero è il **davanzale** |
| `ht 100`, `h 100`, `dav 100` accanto all'apertura | **davanzale** (altezza da terra) |
| nome vicino a "h 300" dentro un locale (`SOGGIORNO` / `h 300`) | **altezza dei muri** 3,00 m (con altezze diverse vale la maggiore, con avviso) |
| `SOGGIORNO`, `CAMERA DA LETTO`, `BAGNO`... dentro uno spazio chiuso dai muri | **nome del locale** (ortografia corretta se somiglia a un locale noto) |

* **Testi esplosi**: se nel disegno le scritte sono state trasformate in linee (EXPLODE), le lettere vengono
  riconosciute dalla loro forma (confronto con i caratteri di alcuni font senza grazie, solo numpy). Si cercano sul
  layer `0` e su quelli chiamati quote/testi/scritte/note/...; altri layer con `--layer-testi "NOME*"`,
  `--no-testi-esplosi` lo spegne. La lettura è buona su cifre e nomi comuni; i testi dubbi si correggono nella tabella.
* I numeri sono **centimetri**, a meno che siano chiaramente millimetri (migliaia) o metri (< 12).
* Una scritta è collegata all'apertura più vicina entro `--raggio-scritte` (default 1,2 m); se il numero della
  larghezza non coincide con quella del simbolo vale solo se la scritta è proprio accanto (< 0,6 m), e nelle
  note dell'apertura compare l'avviso. La larghezza e la posizione restano quelle del disegno.
* Una finestra alta 2 m o più senza davanzale scritto parte da terra (porta-finestra).
* Una quota scritta che non trova nessuna apertura (porta senza simbolo? troppo lontana?) viene segnalata.
* `--altezza-muri` esplicito batte la scritta `h`. Le scritte lette sono anche nella colonna `scritta` della tabella.
* Ogni locale chiuso dai muri è elencato nel riepilogo, con area, nome e altezza. Se i tramezzi non si chiudono
  (un varco senza porta) i locali si fondono in uno solo con tutti i nomi.

## Importare in Cinema 4D

Ci sono due modi. Il secondo è consigliato se usi Corona.

**A. File OBJ** (descritto qui sotto): semplice, ma devi impostare l'importazione (unità, *Flip Z*) e i materiali
sono quelli base del file `.mtl`.

**B. File `_model.json` + script Python in Cinema 4D** (`--json-c4d`): lo script crea oggetti nativi, raggruppati
per tipo (`Murature`, `Infissi` con un gruppo per ogni F01/P01…, `Pavimenti`, `Battiscopa`, `Tetto`…), con un
materiale per tipo (Corona Physical se Corona è installato, altrimenti standard), e risolve da solo assi e
orientamento delle facce. Il file è in **centimetri**, con assi del disegno (X, Y, Z in alto). Lo script è
`c4d/plan2c4d_import.py` (nel progetto): in Cinema 4D *Estensioni → Script Manager → File → Carica* quel file,
*Esegui*, scegli il `*_model.json`; il documento deve essere in centimetri. Lo strumento ha controllato che lo script
accetti il file con un modulo `c4d` simulato; **non è stato provato dentro Cinema 4D** e gli ID dei parametri Corona
dello script dipendono dalla tua versione di Corona.

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
4. Troverai gli oggetti con un materiale base ciascuno (`Muri`, `Tramezzi`, `Pavimento_<locale>`, `Battiscopa`, `Telai_F01`,
   `Ante_P01`, `Vetri_F01`, `Maniglie_P01`, `Tetto`…): sostituisci i materiali con i tuoi. Le normali sono già nel file.
   I materiali sono uno per tipo (tutti i telai usano `Telai`), tranne i pavimenti: uno per locale, per poterli cambiare.

La mesh non ha coordinate UV: usa una proiezione *Cubica* sul materiale.

## Opzioni

| Opzione | Significato |
|---|---|
| `-o`, `--output` | file `.obj` (default: accanto all'input) |
| `--elenca-layer` | mostra i layer e come sono classificati, poi esce |
| `--muri`, `--porte`, `--finestre`, `--pilastri` | layer per categoria (virgole, `*` jolly) |
| `--piano` | quale piano leggere se i layer sono nominati per piano (`P1_`, `pianta2`...) |
| `--accetta-proposte` | usa le proposte di `--elenca-layer` (confidenza media/alta) per i layer non riconosciuti |
| `--includi-nascosti` | usa anche i layer spenti/congelati |
| `--muri-da-blocchi` | leggi come muri anche i blocchi inseriti su un layer di muri |
| `--modalita-muri` | `auto`, `solidi`, `doppia-linea`, `asse` |
| `--spessore-muro` | spessore dei muri disegnati a linea singola (0,30) |
| `--spessore-max` | spessore massimo di un muro a doppia linea (0,60) |
| `--altezza-muri`, `--altezza-porte`, `--davanzale`, `--altezza-finestre` | quote in metri |
| `--no-pavimento`, `--spessore-pavimento`, `--soffitto`, `--no-vetri` | elementi aggiuntivi |
| `--infissi-uniti` | un solo oggetto per tipo di parte (Telai, Ante...) invece di uno per apertura |
| `--infissi` | `dettagliati` (default: telai, ante, maniglie, toppe) o `semplici` (solo vetro) |
| `--no-testi-esplosi`, `--layer-testi` | non cercare / dove cercare le lettere disegnate con le linee |
| `--pavimenti`, `--battiscopa` | layer dei pavimenti / dei battiscopa (virgole, `*` jolly) |
| `--no-tramezzi`, `--no-pavimento-per-locale` | tramezzi dentro `Muri` / una sola lastra di pavimento |
| `--no-vani`, `--vano-max` | non dedurre i vani senza simbolo / larghezza massima (m) |
| `--no-scritte`, `--raggio-scritte` | non leggere i testi / distanza massima tra apertura e quota scritta (m) |
| `--tabella`, `--no-tabella` | applica la tabella delle aperture corretta a mano / non scrivere `NOME_aperture.csv` |
| `--unita` | unità del disegno: `mm`, `cm`, `m`, `in`, `ft` (default: lette dal file) |
| `--unita-output` | unità dell'OBJ: `m` (default), `cm`, `mm` |
| `--area` | converti solo questa zona del disegno |
| `--no-prospetti-auto` | non cercare i prospetti dai layer `Prospetto…` |
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

- **Le unità**: se il file non le dichiara, vengono dedotte dalle dimensioni (con un avviso). Se le dichiara ma
  l'edificio risulterebbe più piccolo di 3 m o più grande di 300 m (succede: un file in cm che dichiara mm) e
  un'altra unità lo rende plausibile, lo strumento usa quella e lo scrive tra gli avvisi. Sono ipotesi: verifica
  `Ingombro muri` nel riepilogo e, se serve, forza con `--unita cm` (con `--unita` non corregge nulla).
- **Un piano alla volta.** Con i layer nominati per piano (`P1_`, `pianta2`) si sceglie con `--piano`; più piani
  sovrapposti o affiancati senza numeri nei nomi vanno separati con `--area` o su file distinti. Ogni piano è un OBJ:
  non vengono impilati.
- Non vengono generati: scale, arredi, tratteggi, quote, comignoli e abbaini. Porte e finestre hanno imbotto, cornice,
  telaio, ante, maniglie e toppa, ma niente cerniere vere: la forma è fatta di scatole (nessun profilo, nessun
  vetro stratificato). La **cerniera** si ricava dall'arco in pianta: senza arco è a sinistra (nota nella tabella).
- **Le scritte**: quelle esplose in linee sono lette per confronto di forma e possono sbagliare (un `1` per una `l`,
  un testo molto piccolo o in un font insolito): i numeri e i nomi letti si vedono nella colonna `scritta` della
  tabella e nel report. Una quota scritta lontana più di `--raggio-scritte` dall'apertura non si collega.
- **I vani senza simbolo** sono dedotti solo tra due testate di muro libere, allineate, con un vuoto di 0,5-2 m:
  un varco tra una testata e un muro che la incrocia non viene visto. Se ne vede uno che non c'è, `MODIFICA_tieni = no`.
- **I locali** sono gli spazi chiusi dai muri: un varco non segnato li fonde in uno (nome con tutti i nomi).
- **Più altezze di muri** (`h 300` in un locale, `h 290` in un altro): tutti i muri prendono la maggiore, con avviso.
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
| Una quota scritta non è stata presa | Guarda l'avviso `Scritta ... non associata`: allarga `--raggio-scritte`, oppure la porta/finestra manca nel disegno. |
| Le lettere esplose sono lette male | Correggi nella tabella (`MODIFICA_*`); `--no-testi-esplosi` le ignora. |
| Un vano compare dove non c'è | Nella tabella `MODIFICA_tieni = no`, oppure `--no-vani`. |
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

Struttura: `dwgfile.py` (apertura DWG/DXF) → `reader.py` (entità → geometrie in metri) → `walls.py`, `openings.py`,
`passages.py` (muri, porte, finestre, vani) → `texts.py`, `vtext.py`, `glyphs.py`, `labels.py` (scritte, locali) →
`elevation.py`, `roof.py` (prospetti, tetto) → `table.py` (tabella delle aperture) → `model.py`, `fixtures.py`,
`floors.py`, `mesh.py` (estrusione e infissi) → `objwriter.py`, `export.py` (OBJ/MTL, JSON per Cinema 4D) →
`qa.py`, `render.py` (immagini di controllo, report). `proposals.py` sono le proposte di `--elenca-layer`.
I template delle lettere (`data/glyph_templates.npz`) sono bitmap dei caratteri di Liberation Sans, FreeSans, DejaVu Sans,
Carlito e Poppins (font con licenza libera).
