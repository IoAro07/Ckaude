# Cronologia delle versioni

La versione in uso la dice `python -m dwg2c4d --version` (e la prima riga di `converti.bat` e il file `*_report.txt`);
deve coincidere con quella di `pyproject.toml` e con la riga in testa al [README](README.md). Dopo ogni aggiornamento
decomprimi il progetto in una **cartella nuova** e controlla che la versione sia quella attesa.

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
