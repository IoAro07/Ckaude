# Cronologia delle versioni

La versione in uso la dice `python -m dwg2c4d --version` (e la prima riga di `converti.bat` e il file `*_report.txt`);
deve coincidere con quella di `pyproject.toml` e con la riga in testa al [README](README.md). Dopo ogni aggiornamento
decomprimi il progetto in una **cartella nuova** e controlla che la versione sia quella attesa.

## [0.6.0] — 2026-10-09

**Prospetti disegnati altrove** e tavole con più disegni.

- **Prospetti non allineati** (di lato, su un altro foglio, ruotati; facciate nord, sud, est, ovest, a qualunque angolo):
  ogni vista-prospetto è abbinata alla facciata della pianta per confronto della fila di finestre e porte (centri,
  larghezze, spostamento libero lungo la facciata), con una soglia di significatività, un margine sulla facciata
  seguente e almeno 3 aperture abbinate; le altezze di davanzale e architrave delle aperture abbinate vengono dal
  prospetto. Il nord del foglio si ricava dai prospetti con titolo che si abbinano senza ambiguità, e solo allora i titoli
  (`PROSPETTO SUD`...) risolvono i casi ambigui; due prospetti della stessa facciata non si sovrappongono; i colmi dai
  prospetti nord/sud; le viste escluse dal giardino.
- **Simboli senza layer**: porte e finestre dei prospetti da linee di qualunque layer (rettangoli, archi, telai
  annidati, campiture di vetro, ante e persiane), quota del pavimento da `+0,00`/porta/linea di terra/figura, piani
  da quote e solai (`elevsymbols.py`).
- **Altezza dei muri dal prospetto** quando quella in uso (predefinita) non contiene le finestre del prospetto:
  interpiano o linea di gronda; solo da un prospetto sicuro o da due che concordano; gli infissi dei prospetti allineati
  seguono l'altezza nuova.
- **Revisione indipendente** del confronto: le aperture del muro opposto non prendono quote dal prospetto sbagliato, i
  tipi (porta/finestra) risolvono i pareggi tra campate, piani interrati e titoli `1° PIANO`/`PIANO 2`, linea di
  gronda vicina alle finestre, due piani sovrapposti senza quote, larghezza del prospetto contro l'edificio, lato
  dichiarato nel riferimento del disegno quando il nord non si sa, un errore nel confronto non ferma più la conversione.
- **Foglio senza pianta** (solo prospetti, sezioni, planimetria generale): messaggio chiaro con `NOME_viste.png`
  invece di un modello senza senso.
- Giardino: niente più oltre 25 m dalla pianta (o dal lotto in cui è disegnata).
- **Viste del foglio** molto più robuste sulle tavole vere: cornici del foglio e dei titoli, linee lunghe e segni
  isolati che non uniscono più due disegni, titoli sopra o sotto il disegno, piante dentro l'inquadramento (copie
  riconosciute dai testi e dalle linee), tipo dal contenuto, riquadro = quello del disegno.
- **Unità**: voto dall'altezza dei testi; una tavola grande (più disegni) non è più scartata; l'intestazione è
  sostituita solo con un margine di voti. (Test6 e Test7, in cm con intestazione mm, ora sono letti in cm.)
- Meno rumore: gli avvisi di ezdxf sugli oggetti che non sa copiare (centinaia di migliaia di righe) non escono più.
- `Config`: l'analisi del foglio non entra nell'uguaglianza.

## [0.5.0] — 2026-10-08

**L'analisi del foglio**: lo strumento capisce disegni con i layer disordinati guardando la geometria.

- **Unità** dai numeri (mediana delle quote, archi di porta, dimensione): l'intestazione del file è il voto più debole.
- **Viste del foglio** (pianta, copertura, prospetti, sezione, planimetria generale) separate dallo spazio vuoto e
  dai titoli, o dalla forma; immagine `NOME_viste.png`; la pianta da convertire è la più grande che non sia il lotto
  né una copia; `--vista N` per sceglierne un'altra.
- **Muri senza layer dei muri**: si sceglie il layer (o i layer) le cui linee hanno la forma dei muri, compresi i
  muri a due linee parallele con le estremità aperte; arredi chiusi e corpetti isolati sono ignorati.
- **Porte dall'arco di rotazione** su qualunque layer; i varchi in un muro esterno senza simbolo diventano finestre.
- `--no-analisi` la salta. Quello che indica l'utente (`--unita`, `--muri`, `--area`) non viene mai cambiato.
- Più veloce: i layer sono classificati una volta sola, non a ogni entità, e il disegno non viene riletto nel
  secondo passaggio (file grandi: da ~100 s a ~60 s).

## [0.4.0] — 2026-10-08

Il **giardino**: tutto ciò che sta fuori dai muri.

- **Terreno**: le campiture (retini) fuori dai muri diventano `Pavimentazione`, `Prato`, `Terreno` e `Bordi`, riconosciute
  dal nome del layer, dal nome del retino e dal colore (verde = prato, azzurro = acqua, altro = pavimentazione), nell'ordine in
  cui AutoCAD le mostra, senza sovrapposizioni.
- **Piscina scavata**: `Vasca` (guscio chiuso, con il bordo scoperto del disegno) e `Acqua`.
- **Piante e arredi** dai blocchi (`Siepe`, `Albero`, `Cespuglio`, `Sdraio`…): segnaposto sostituibili, ciascuno con la sua
  sigla (S01, A01, C01, E01) e un gruppo con l'asse al centro della base, come gli infissi.
- **Altezze** dal prospetto (blocchi o campiture, righe sovrapposte insieme), altrimenti predefinite; tabella
  `NOME_giardino.csv` con colonne `MODIFICA_*` e opzione `--tabella-giardino`.
- Opzioni: `--no-giardino`, `--area-giardino`, `--tabella-giardino`, `--profondita-piscina`, `--altezza-alberi`,
  `--altezza-siepi`, `--altezza-cespugli`; `--no-tabella` salta anche la tabella del giardino.
- Immagine di controllo e riepilogo con il giardino; script di Cinema 4D con i nuovi gruppi e materiali.
- Correzioni emerse dalla revisione: punto base dei blocchi, sfumature, matrici (MINSERT) e blocchi contenitore, zone dei
  prospetti vicini alla pianta, buchi nel terreno, layer `Pavimentazione interna` che resta pavimento dei locali.
- `Pavimentazione` e `Pavimenti esterni` non sono più i pavimenti dei locali, ma la pavimentazione del giardino.

## [0.3.2] — 2026-10-07

- `GUIDA_DISEGNO.md`: come preparare il DXF (layer, simboli, scritte, prospetti, tetto, lista di controllo), nel pacchetto.

## [0.3.1] — 2026-10-07

- I muri perimetrali sono tagliati a metà spessore: `Muri_esterno` (metà verso l'esterno) e `Muri_interno` (metà verso i
  locali, più i muri tra i locali), due solidi chiusi con il loro materiale. `--muri-uniti` rinuncia alla divisione.

## [0.3.0] — 2026-10-07

- Ogni infisso è un gruppo con l'**asse al centro della base** (X lungo il muro, Z verso l'esterno): si sostituisce con un
  modello proprio.
- I pavimenti per locale arrivano fino alla faccia esterna dei muri perimetrali.
- Il fondo dei muri è chiuso; due materiali (esterno / interno).

## [0.2.1] — 2026-10-07

- `converti.bat` ed `elenca_layer.bat` usano sempre il codice della cartella del progetto; `installa.bat` pulisce `build`
  e reinstalla; la versione e il percorso in uso vengono stampati.

## [0.2.0] — 2026-10-07

- Infissi dettagliati (imbotto, cornice, telai, ante, maniglie, toppe) e tabella delle aperture `NOME_aperture.csv` con
  colonne `MODIFICA_*`.
- Quote scritte, nomi e altezze dei locali dai testi (anche esplosi in linee).
- Pavimenti per locale, battiscopa, tramezzi, vani senza simbolo.
- Immagini di controllo e report; prospetti trovati dal nome del layer, layer misto porte/finestre, proposte di layer con
  confidenza, più piani (`--piano`).
- File `.bat` per Windows e script di importazione per Cinema 4D; gruppi di muri senza aperture (pianta del tetto) esclusi da
  soli; tetto automatico se c'è il layer.

## [0.1.0] — 2026-10-06

- Prima versione: da DWG/DXF a OBJ + MTL per Cinema 4D: muri (solidi, doppia linea, asse), porte e finestre, pavimento,
  quote dai prospetti, tetto dalla pianta del tetto, origine al centro, JSON per Cinema 4D.
