# CLI — `scripts/convert_one.py`

Convertitore PDF → EPUB3 da terminale.

## Uso rapido

```bash
python scripts/convert_one.py path/to/book.pdf
# Output: path/to/book.epub
```

## Opzioni

| Flag                  | Default | Descrizione                                                            |
|-----------------------|---------|------------------------------------------------------------------------|
| `input`               | —       | PDF sorgente (obbligatorio)                                            |
| `output`              | auto    | EPUB di destinazione (default: `<input>.epub`)                         |
| `--quant`             | `int4`  | Quantizzazione: `none`, `int8`, `int4`                                 |
| `--dpi`               | `300`   | Risoluzione rendering per i crop                                        |
| `--pages-per-batch`   | `20`    | Pagine per batch di inferenza OCR                                       |
| `--title`             | auto    | Titolo del libro (default: nome file PDF)                              |
| `--author`            | `Unknown` | Autore                                                              |
| `--language`          | `it`    | Codice lingua ISO 639-1                                                |
| `--no-eink-optim`     | off     | Disabilita ottimizzazione WebP/E-ink                                    |
| `--chapter-pages N`   | `None`  | Raggruppa pagine in capitoli di N pagine (se il libro è senza heading)  |
| `--resume` / `--no-resume` | `--resume` | Controlla il checkpoint OCR                                       |
| `-v`, `--verbose`     | off     | Log DEBUG                                                              |

## Checkpoint & Resume

Quando converti PDF di grandi dimensioni (centinaia di pagine), l'OCR è la fase
più lenta e può essere interrotta da `Ctrl+C`, crash OOM, o black-out elettrico.

A partire dalla versione corrente:

* Ad ogni batch OCR completato, lo stato viene salvato in
  `<pdf_dir>/.relictoepub_checkpoints/state.json` (atomic write
  `tmp → fsync → rename`).
* Rilanciando lo stesso comando sullo stesso PDF, i batch già completati
  vengono saltati: solo le pagine rimanenti vengono OCR-ate.
* Se il PDF sorgente è cambiato (SHA256 diverso) il resume si rifiuta con
  un errore esplicito. Usa `--no-resume` per forzare la riesecuzione.

### Esempi

```bash
# Prima run — tutto da zero
python scripts/convert_one.py libro.pdf

# Ctrl+C mid-way?  Rilancia lo stesso comando, prosegue da dove era.
python scripts/convert_one.py libro.pdf
# → ♻️ Checkpoint trovato: 12/35 batch già completati

# Forza la ripartenza da zero (es. dopo cambio modello OCR)
python scripts/convert_one.py libro.pdf --no-resume

# Cancellare manualmente lo stato
rm -rf libro_dir/.relictoepub_checkpoints/
```

### Posizione del checkpoint

Il checkpoint è sempre collocato in
`<stessa cartella del PDF sorgente>/.relictoepub_checkpoints/`.

Vantaggi:
- Sopravvive a reboot della macchina (non usa `/tmp`).
- Coerente: ogni PDF ha il suo checkpoint, niente confusione.
- Git-ignore-abile (`.relictoepub_checkpoints/` è una directory hidden).

### Cleanup automatico al termine

Per ora lo stato checkpoint **resta su disco** dopo `done`, utile come cache
in caso l'utente voglia ri-eseguire con parametri diversi. Cancellalo
manualmente quando non serve più.

### Limitazioni note

- Il checkpoint copre **solo la fase OCR**. Le fasi di rendering, cropping,
  WebP optimization e compilazione EPUB non sono checkpoint-ate (sono veloci
  e/o idempotenti).
- Cambiare `--dpi`, `--pages-per-batch` o `--quant` su una run ripresa
  **rende invalido** lo stato batch cached: il resume salva con i parametri
  della prima run, quindi i batch successivi useranno quelli.
  Per cambiar parametri, usa `--no-resume`.

## Cancel & Interruzione cooperativa

Sia la CLI che la UI Gradio supportano l'interruzione cooperativa della
pipeline OCR:

* **CLI**: `Ctrl+C` (SIGINT) viene catturato e propaga un
  `PipelineCancelledError`. Lo stato checkpoint viene salvato se almeno un
  batch era stato completato.
* **UI Gradio**: pulsante **⏹ Stop** (rosso) accanto a **🚀 Converti**. Clic
  per interrompere l'OCR corrente in modo cooperativo. I batch completati
  sono persistiti nel checkpoint; rilanciando la stessa conversione la
  pipeline riprende automaticamente (se "♻️ Ripresa conversione" è attivo).

In entrambi i casi il cancel è **cooperativo**: la pipeline si arresta al
prossimo confine di batch (o al prossimo yield dello streaming OCR).
Nessun dato già renderizzato va perso.
