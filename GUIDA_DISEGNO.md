# Come preparare il disegno (DWG/DXF) per ottenere il modello migliore

Lo strumento funziona con quasi qualsiasi disegno, ma ogni scelta che fai in AutoCAD toglie un'ipotesi al programma.
Questa guida dice **cosa disegnare, come chiamare i layer e dove mettere i prospetti**. Tutto ciò che qui è descritto
è quello che il codice legge davvero; le cose che non legge sono elencate in fondo.

Il principio: **un layer = una cosa**, nominato con le parole che il programma riconosce, disegnato **in scala 1:1**
nelle unità giuste. Il resto (quote, nomi dei locali, altezze) lo puoi scrivere come testo vicino all'elemento.

Se il disegno **non è tuo** e i layer sono disordinati, non serve sistemarlo prima: da riga di comando il programma
guarda il foglio (unità, viste, muri, porte; vedi nel [README](README.md) *Disegni con i layer disordinati*) e dice
cosa ha capito. Ma più il disegno segue questa guida, più il risultato è esatto: l'analisi è un ripiego.

---

## 1. Le dieci regole che contano di più

1. **Un layer per ogni categoria**: muri, tramezzi, porte, finestre (o un solo layer `Infissi`), pilastri, tetto,
   prospetti, testi. Mai un muro e un arredo sullo stesso layer.
2. **Sul layer dei muri solo muri.** Muretti, bordi di piscina, pavimentazioni, siepi e cordoli diventano muri.
3. **Disegna in scala 1:1** e imposta le unità del file (`UNITS` → centimetri o metri). Se le unità sono dichiarate
   male il programma prova a correggerle e lo scrive, ma è un'ipotesi.
4. **I muri devono chiudere i locali.** Se un muro ha un varco senza porta, i due locali si fondono in uno solo
   (pavimenti, nomi e muri a metà spessore ne risentono). Un varco largo 0,5–2 m tra due testate di muro viene
   riconosciuto come passaggio; più largo, o con un muro che lo incrocia, no: chiudilo o mettici un simbolo.
5. **Porte con l'arco di rotazione** (il quarto di cerchio), finestre senza arco. L'arco dà il verso di apertura,
   la cerniera e il numero di ante.
6. **Il simbolo di porta/finestra deve toccare il muro** che apre. Se è staccato, l'apertura non viene creata.
7. **Scrivi i nomi dei locali dentro i locali** (`SOGGIORNO`, `CAMERA`, `BAGNO`...) e l'altezza accanto: `h 300`.
8. **Testi veri (TEXT/MTEXT), non esplosi.** I testi esplosi in linee si leggono per confronto di forma e possono
   sbagliare (`1` per `l`). Se li hai già esplosi funziona lo stesso, ma controlla.
9. **Prospetti nello stesso file, in proiezione sulla pianta** (vedi §5): sud sotto la pianta, nord sopra, con le
   **stesse coordinate X**, e nella stessa scala.
10. **Salva in DXF** (versione 2013 o successiva) se puoi: evita la conversione del DWG e qualunque dubbio sul
    formato. Incorpora gli **xref** nel disegno: i riferimenti esterni non vengono letti.

---

## 2. Layer consigliati

Il programma divide il nome del layer in parole (separatori: tutto ciò che non è una lettera) e cerca queste:

| Layer consigliato | Contiene | Parole riconosciute (prefisso) |
|---|---|---|
| `P1_MURI` | muri perimetrali e portanti | `mur`, `paret`, `wall`, `tamponam` |
| `P1_FONDELLI` | tramezzi sottili: diventano l'oggetto `Tramezzi`, con materiale a parte | `fondell`, `tramezz`, `partit`, `divisor` |
| `P1_INFISSI` | porte **e** finestre insieme (le distingue dalla forma: arco = porta) | `infiss`, `serrament` |
| `P1_PORTE` / `P1_FINESTRE` | porte e finestre separate (più sicuro: nessuna deduzione) | `porta`/`porte`/`door`, `finestr`/`window`/`glaz` |
| `P1_PILASTRI` | pilastri e colonne (polilinee chiuse, campiture, cerchi) | `pilast`, `colonn`, `column` |
| `P1_PAVIMENTI` | facoltativo: un poligono chiuso per ogni locale | `pavim`, `floor`, `solai` |
| `P1_BATTISCOPA` | facoltativo: linee lungo le pareti | `battiscop`, `skirt`, `zoccol` |
| `TETTO` | pianta del tetto: solo contorno, colmi, displuvi, compluvi | `tett`, `roof`, `copertur` |
| `PROSPETTO_SUD`, `PROSPETTO_NORD` | prospetti (vedi §5) | `prospett`, `elevat`, `facciat` |
| `GIARDINO_PRATO`, `GIARDINO_PAVIMENTAZIONE`, `PISCINA`, `VERDE`, `ARREDO_ESTERNO` | il giardino (vedi §6bis) | `prato`, `pavimentazion`, `piscin`, `verde`, `estern` |
| `QUOTE`, `TESTI`, `ARREDI`, `SEZIONI` | tutto il resto: **ignorato** | — |

Cose da sapere sui nomi:

* Maiuscole e minuscole non contano; `MURI_PORTANTI` è un layer di muri (non di porte).
* I layer chiamati `Prospetto…`, `Sezione…`, `Arredo…`, `Quote…`, `Testi`, `Tetto`, `Verde` **non vengono mai presi
  per muri, porte o finestre**, nemmeno se dentro c'è la parola "parete" o un blocco `Porta Asciugamani`. I layer del
  giardino (`Verde`, `Prato`, `Piscina`, `Pavimentazione`, `Esterno`…) servono al giardino (§6bis); `Pavimentazione` e
  `Pavimenti esterni` **non** sono i pavimenti dei locali.
* I layer **spenti o congelati vengono saltati**: è il modo più rapido per escludere dal modello ciò che non serve.
* Se i tuoi nomi sono diversi (`A-MURI-ESTERNI`, `SERR_P`...) non è un problema: `elenca_layer.bat` mostra come li ha
  letti e propone il resto; oppure li indichi con `--muri "A-MURI*" --porte "SERR_P" --finestre "SERR_F"`.
* Se porte e finestre sono sullo stesso layer funziona, ma è l'unico caso in cui il programma **deduce** il tipo:
  tienili separati se vuoi zero sorprese. Un blocco chiamato `PORTA90` o `FINESTRA120` viene riconosciuto su
  qualunque layer.

### Più piani

Metti il numero del piano **all'inizio del nome, separato da `_`, `-`, `.` o spazio**: `P1_MURI`, `P2_MURI`,
`PIANTA1_INFISSI`, `Piano 3 muri`. Si legge **un piano alla volta**: di default il più basso, con `--piano 2` un altro;
ogni piano diventa un OBJ a sé, non vengono impilati. I layer senza numero (tetto, prospetti, testi) valgono per
tutti. Attenzione: `pianta1muri` (senza separatore) non porta il numero del piano; scrivi `pianta1_muri`.

---

## 3. Come disegnare ogni elemento

### Muri

Tre modi, tutti accettati (`--modalita-muri auto` li riconosce da solo):

* **Polilinee chiuse o campiture (HATCH/SOLID)**: il modo più affidabile. Il contorno esterno e quello interno di un
  muro ad anello formano lo spessore.
* **Due linee parallele** (LINE, polilinee aperte, archi): le testate aperte vengono chiuse da sole, i distacchi di
  qualche millimetro ricuciti. Spessore massimo letto: 60 cm (`--spessore-max` per muri più spessi).
* **Una linea sull'asse**: riceve uno spessore fisso (`--spessore-muro`, 30 cm). Va bene solo per prove veloci.

Per avere **muri perimetrali divisi in metà esterna e metà interna** (due materiali) i muri devono racchiudere almeno
un locale: serve un "dentro" da cui misurare. Senza locali chiusi resta un solo oggetto `Muri`.

Pulizia consigliata prima di salvare: `OVERKILL` (linee duplicate/sovrapposte), `AUDIT`, `PURGE`. Linee doppie
sovrapposte non danno errore ma sporcano i contorni.

### Porte

* **Simbolo con arco di rotazione**: il centro dell'arco è la cerniera, la maniglia va sul lato opposto, due archi =
  porta a due ante. Senza arco la cerniera non si deduce: anta incernierata a sinistra, con una nota nella tabella.
* **Blocco o linee sciolte** vanno bene entrambi. Un blocco chiamato `PORTA…` è riconosciuto sempre.
* Il muro può essere **interrotto** nel punto della porta oppure **continuo** con il simbolo sopra: il programma gestisce
  tutti e due (ricostruisce l'architrave e taglia solo la quota giusta).
* Il **passaggio senza porta** (vano) si può anche non disegnare: vedi regola 4. Compare nella tabella come `V01`
  e, se non c'è davvero, lo escludi con `MODIFICA_tieni = no`.

### Finestre

* Simbolo di finestra: rettangolo/linee sul muro. Le **linee che tagliano il simbolo di traverso al muro** sono le
  divisioni tra le ante: una linea = 2 ante, due linee = 3 ante. Disegna quelle linee se vuoi il numero giusto di ante
  (altrimenti l'anta è una).
* Una finestra alta 2 m o più senza davanzale scritto parte da terra (portafinestra).
* **Finestre sul loro layer**, mai sul layer dei muri: se le linee del serramento sono sul layer dei muri, il muro
  viene spezzato lì e l'apertura non si riconosce.

### Pavimenti, battiscopa

Se non fai niente, ogni locale chiuso dai muri riceve il suo pavimento (esteso sotto le porte e fino alla faccia
esterna dei muri perimetrali). Se vuoi il controllo, disegna sul layer `PAVIMENTI` **un poligono chiuso per
locale**, con il nome del locale scritto dentro; un poligono dentro un altro lo scava (altra finitura, es. il piatto
doccia). Per il battiscopa: una linea lungo il muro sul layer `BATTISCOPA` (alto 8 cm, si ferma alle porte).

---

## 4. Scritte: quote, nomi, altezze

I testi sono letti da TEXT, MTEXT, attributi di blocco e multileader. Scrivili **vicino all'elemento** (entro 1,2 m)
e in **centimetri**:

| Scrittura | Significato |
|---|---|
| `120x150` o `120 x 150` o `L120 H150` | larghezza × altezza dell'apertura più vicina |
| `120x150x90` | come sopra, il terzo numero è il davanzale |
| due righe: `120` sopra `150` | larghezza e altezza |
| `ht 100`, `h 100`, `dav 100` accanto a una finestra | davanzale (altezza da terra) |
| `SOGGIORNO` dentro il locale + `h 300` accanto | nome del locale e **altezza dei muri** 3,00 m |

* L'ordine di priorità delle quote è **tabella CSV > scritta > prospetto > valori predefiniti**.
* La larghezza e la posizione di un'apertura restano sempre quelle del **simbolo disegnato**: la scritta non le sposta
  (se non coincide lo dice nelle note).
* Se scrivi `h 300` in locali diversi con valori diversi, tutti i muri prendono il maggiore (con avviso).
* Numeri in centimetri; le migliaia sono lette come millimetri, i valori sotto 12 come metri.
* Una scritta senza porta/finestra vicina viene segnalata (`Scritta ... non associata`): significa che manca
  il simbolo o è oltre 1,2 m (`--raggio-scritte`).

---

## 5. Prospetti: dove metterli e come disegnarli

Servono per le **altezze di porte e finestre** (davanzale, architrave) e per la **pendenza del tetto**. Se mancano, si
usano i valori predefiniti o le scritte.

**Posizione.** Il modo più sicuro è disegnarlo **in proiezione sulla pianta**, come nelle tavole classiche (così è letto
anche dalle regole semplici, senza analisi del foglio):

* **prospetto sud** (facciata in basso nella pianta) → **sotto** la pianta;
* **prospetto nord** (facciata in alto nella pianta) → **sopra** la pianta;
* in entrambi i casi con le **stesse coordinate X** della pianta e nella **stessa scala**: la finestra che nel prospetto
  sta a x = 350 deve stare a x = 350 anche in pianta. Il programma abbina ogni simbolo del prospetto all'apertura della
  pianta con lo stesso intervallo di X (la più esterna).
* Non specchiarli. Se in una tavola vera li metti di lato o su un altro foglio, il programma li legge lo stesso
  **confrontando la fila delle finestre con le facciate della pianta** (vedi il README, *Prospetti disegnati altrove*):
  perché funzioni scrivi un **titolo** (`PROSPETTO SUD`, `PROSPETTO NORD`...), tieni i prospetti nella **stessa scala**
  della pianta, e disegna **almeno 3 aperture riconoscibili** per facciata.

**Come riconoscerli.** Metti tutto il prospetto su layer che iniziano con `Prospetto` (`PROSPETTO_SUD`, `prospetto1`…):
li trova da soli. Devono contenere **porte e/o finestre riconoscibili** (blocchi `Porta…`/`Finestra…` o layer
`Porte`/`Finestre`, anche dentro un layer `Prospetto…`): un prospetto interno (parete di cucina) senza aperture viene
scartato di proposito. Se preferisci, indichi tu la zona con `--prospetto XMIN,YMIN,XMAX,YMAX[,QUOTA_Y]`.

**Quota zero.** Scrivi **`+0,00`** (o `±0.00`) accanto al segno di livello del pavimento finito: è la fonte più sicura,
anche se il prospetto non ha porte. Altrimenti il pavimento è il **fondo della porta più bassa** (o la linea di terra);
con `--prospetto` indichi la quota Y come quinto valore. Se il prospetto mostra più piani, scrivi anche `+3,20`...

**Cosa disegnare dentro.** I simboli di porte e finestre come nella pianta (blocchi `Porta…`/`Finestra…` o linee sui
layer `Porte`/`Finestre`; telaio e vetro annidati contano come uno solo) e, per il tetto, i **colmi come linee
orizzontali** con la stessa estensione X dei colmi della pianta del tetto.

**Altezza dei muri.** Se le finestre del prospetto salgono più in alto dei muri e l'altezza dei muri non è scritta (`h 300`
nei locali) né indicata, viene presa dal prospetto: dall'interpiano o dalla **linea orizzontale che chiude il muro in
alto** (gronda/parapetto): disegnala lunga quanto la facciata.

**Facciate est e ovest.** Con l'analisi del foglio sono lette come le altre; i prospetti trovati per nome di layer o
indicati con `--prospetto` restano solo nord e sud in proiezione.

---

## 6. Il tetto

Il tetto è **ricostruito dalla pianta del tetto**, non inventato: se la pianta è confusa, il tetto lo sarà.

* Layer `TETTO` (o `COPERTURA`, `ROOF`). Dentro **solo**:
  1. il **contorno di gronda** (polilinea chiusa o linee che chiudono),
  2. i **colmi**,
  3. i **displuvi e i compluvi** (linee diagonali).
  Travi, quote, griglie o campiture su quel layer spezzano le falde.
* **Posizione**: la pianta del tetto è centrata sul centro dei muri. Disegnala dove vuoi nel file (meglio staccata dalla
  pianta, sul layer `TETTO`); se la gronda non è simmetrica rispetto ai muri, serve `--sposta-tetto DX,DY`. Se è
  disegnata lontano indica `--area-tetto`.
* **Pendenza**: viene dedotta dai **colmi orizzontali del prospetto** (stessa estensione X dei colmi in pianta): ogni
  falda prende la sua. Senza prospetto vale `--pendenza GRADI` oppure 25°. Se le falde hanno pendenze diverse,
  il prospetto è l'unico modo di dirlo.
* **Gronda**: alla quota dei muri, senza sporgenza oltre il contorno interno; il tetto è un solido pieno (spessore
  15 cm).
* Non vengono generati abbaini, comignoli, lucernari.

---

## 6bis. Giardino e aree esterne

Quello che sta **fuori dai muri** diventa terreno, piscina, siepi, alberi e arredi (`--no-giardino` lo esclude).

* **Terreno: campiture (HATCH) fuori dai muri.** Una per tipo di superficie. Cosa sono lo decide, nell'ordine, il
  **nome del layer** (`Prato`, `Pavimentazione`, `Terrazza`, `Piscina`…), il **nome del retino** (`GRASS`…) e il **colore**:
  **verde = prato, azzurro = acqua, qualunque altro colore = pavimentazione**, bianco = ignorato. Se i colori dei tuoi
  retini sono diversi, metti i pezzi su layer con il nome giusto.
* **Disegna le campiture nell'ordine in cui si coprono**: vale l'ordine che AutoCAD mostra (quello sopra copre quello
  sotto, anche dopo un `DRAWORDER`). Una campitura a sfumatura vale per il suo primo colore. Un retino con un buco (un
  tavolo, un cespuglio) va bene: il buco prende ciò che lo circonda.
* **La piscina**: una campitura azzurra per l'acqua. Se la pavimentazione attorno ha un buco **un po' più grande
  dell'acqua** (fino a 80 cm), quello è il bordo vasca; altrimenti la vasca ha le pareti di 15 cm. Profondità 1,5 m
  (`--profondita-piscina`).
* **Strisce sottili** (meno di 35 cm: cordoli, muretti disegnati a campitura) diventano `Bordi`, piatti. Muretti e
  recinzioni disegnati solo a linee **non** vengono costruiti.
* **Alberi, siepi, cespugli, arredi: blocchi**, con un nome che dica cosa sono (`Albero`, `Siepe`, `Cespuglio 1`,
  `Sdraio`, `Tavolo`…) o su un layer `Verde`/`Giardino`/`Arredo esterno`. Il contorno del blocco dà la dimensione del
  segnaposto, **l'inserimento la posizione e la rotazione**. Fuori dai muri: un blocco dentro casa è un arredo e si ignora.
  Un blocco il cui nome contiene `Prospetto` è un disegno per il prospetto, non una pianta della pianta.
* **Altezze: dal prospetto.** Nel prospetto sud/nord (§5) disegna gli stessi alberi e le stesse siepi: un blocco
  `Albero Prospetto` dà l'altezza degli alberi, la siepe in prospetto (blocchi `Siepe` o campiture larghe quanto quella
  in pianta, anche in due righe sovrapposte) dà quella delle siepi. Senza prospetto: 4,5 m alberi, 1,2 m siepi, 0,9 m
  cespugli, o `--altezza-alberi`/`--altezza-siepi`/`--altezza-cespugli`, o la tabella `NOME_giardino.csv`.
* **Tieni il giardino vicino alla casa.** Le campiture a più di 3 m dall'edificio e dal resto del giardino (la pianta del
  tetto, un altro disegno) si ignorano; per un giardino staccato indica `--area-giardino`.
* **Un solo livello**: il terreno è piano; il disegno 2D non dice dove sale o scende.

## 7. Cosa non viene letto

* Arredi interni, sanitari, scale, quote, tratteggi decorativi: ignorati (e se sono blocchi sul layer dei muri,
  scartati con un avviso). Il giardino (campiture e blocchi fuori dai muri) si legge: §6bis.
* Riferimenti esterni (xref).
* Più piani nello stesso modello: un OBJ per piano.
* Prospetti est/ovest, prospetti ruotati o disposti altrove.
* Sezioni (vengono ignorate).
* Blocchi dinamici o anonimi (`*U123`) come indizio per il tipo: usa layer o nomi di blocco chiari.

---

## 8. Come controllare il risultato (e correggerlo senza toccare il disegno)

1. **`elenca_layer.bat`** (o `dwg2c4d file.dwg --elenca-layer`): mostra le unità dichiarate, come ogni layer è letto, i
   piani trovati e le **proposte** per i layer che non conosce. Se un layer è letto male, lo correggi qui.
2. **`converti.bat`**: scrive accanto al disegno:
   * `*_controllo_pianta.png`: la pianta con i muri, le aperture (sigle F01, P01…), le scritte lette e i locali.
     **Guardala prima di aprire Cinema 4D**: se un'apertura è nel posto sbagliato o manca, si vede qui;
   * `*_anteprima_3d.png`: l'anteprima del modello;
   * `*_report.txt`: riepilogo e **avvisi** (unità, aperture non abbinate, prospetti trovati, tetto, gruppi esclusi);
   * `*_aperture.csv`: una riga per apertura con **l'origine di ogni misura** (simbolo, scritta, prospetto, predefinita);
   * `*_giardino.csv`: una riga per albero, siepe, cespuglio e arredo esterno, con altezza e **da dove viene**
     (prospetto o predefinita); si corregge con `MODIFICA_*` e `--tabella-giardino`;
   * `*.obj/.mtl` e `*_model.json` (per lo script di Cinema 4D).
3. **Correggere**: apri `*_aperture.csv` in Excel, compila le colonne `MODIFICA_*` (tipo, larghezza, davanzale,
   altezza, ante, cerniera, `tieni = no`) e rilancia con `--tabella file_aperture.csv`.
4. Domande da farsi leggendo il report:
   * "Ingombro muri" è plausibile (metri)? Altrimenti unità sbagliate.
   * Quanti locali ha trovato? Se sono meno del previsto, un muro ha un varco.
   * Quante aperture e di che tipo? Una finestra scambiata per porta dice che il layer misto è stato dedotto male.
   * "Prospetto sud: N di M aperture"? Se M ≠ N, le X di prospetto e pianta non coincidono.

---

## 9. Lista di controllo prima di salvare

- [ ] Layer separati (muri, tramezzi, infissi o porte+finestre, pilastri, tetto, prospetti, testi)
- [ ] Layer con parole riconoscibili e numero del piano a inizio nome (`P1_`)
- [ ] Solo muri sul layer dei muri; arredi su layer propri o spenti
- [ ] Disegno 1:1, unità impostate (`UNITS`)
- [ ] Muri che chiudono i locali; passaggi larghi 0,5–2 m o con simbolo
- [ ] Porte con l'arco; finestre con le linee di divisione ante; simboli a contatto con il muro
- [ ] Nome del locale e `h 300` scritti dentro ogni locale
- [ ] Quote delle aperture scritte vicino (`120x150`, `ht 100`) — facoltative se c'è il prospetto
- [ ] Prospetto sud sotto e nord sopra la pianta, stesse X, stessa scala, con almeno una porta
- [ ] Pianta del tetto sul layer `TETTO`: solo contorno, colmi, displuvi, compluvi
- [ ] Giardino: campiture verdi = prato, azzurre = acqua, altre = pavimentazione (o layer `Prato`/`Pavimentazione`/`Piscina`);
      alberi, siepi, cespugli come blocchi con il nome giusto, fuori dai muri; gli stessi in prospetto per le altezze
- [ ] Testi non esplosi; xref incorporati; `OVERKILL`/`PURGE` fatti
- [ ] Salvato come DXF (2013 o successivo) oppure DWG con ODA File Converter / LibreDWG installato
