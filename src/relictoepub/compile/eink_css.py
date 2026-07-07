"""CSS ottimizzato per display E-ink.

Caratteristiche di un foglio di stile E-ink-friendly:

* **Niente animazioni, niente transizioni**: i device E-ink hanno
  un refresh lento (~400 ms per pagina) e ogni cambio di stile
  causa un ghost.
* **Font serif**: più leggibile dei sans-serif sul contrasto modesto
  dei pannelli E-ink.
* **Dimensioni relative**: ``em`` e ``%`` perché la dimensione del
  testo può essere regolata dall'utente nell'e-Reader.
* **Contronto alto**: sfondo quasi-bianco, testo quasi-nero.
* **Immagini ``display: block``**: per evitare lo spazio inline
  che gli e-Reader a volte aggiungono sotto le immagini.
"""

EINK_CSS = """
/* RelicToEpub — base stylesheet for E-ink readers (Kindle, Kobo, reMarkable) */

@namespace epub "http://www.idpf.org/2007/ops";

html {
  margin: 0;
  padding: 0;
}

body {
  font-family: "Georgia", "Times New Roman", serif;
  font-size: 1em;
  line-height: 1.5;
  color: #111111;
  background-color: #fafafa;
  margin: 1em;
  padding: 0;
  text-align: justify;
  hyphens: auto;
  -epub-hyphens: auto;
}

h1, h2, h3, h4, h5, h6 {
  font-family: "Helvetica", "Arial", sans-serif;
  color: #000000;
  page-break-after: avoid;
  page-break-before: auto;
  margin-top: 1.2em;
  margin-bottom: 0.6em;
  line-height: 1.2;
  font-weight: 600;
}

h1 { font-size: 1.5em; text-align: left; margin-top: 0; }
h2 { font-size: 1.25em; }
h3 { font-size: 1.1em; }
h4, h5, h6 { font-size: 1em; }

p {
  margin: 0.5em 0;
  text-indent: 1.2em;
}

p:first-of-type,
h1 + p, h2 + p, h3 + p, h4 + p {
  text-indent: 0;
}

/* Figure responsive: si adattano alla larghezza del viewport */
img, figure, figure img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 1em auto;
  page-break-inside: avoid;
}

figure {
  margin: 1.5em 0;
  text-align: center;
}

figcaption {
  font-size: 0.9em;
  font-style: italic;
  color: #444;
  margin-top: 0.5em;
}

/* Tabelle */
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
  page-break-inside: avoid;
}

th, td {
  border: 1px solid #555;
  padding: 0.4em 0.6em;
  text-align: left;
  vertical-align: top;
}

th {
  background-color: #efefef;
  font-weight: 600;
}

/* Blockquote (citazioni) */
blockquote {
  margin: 1em 1.5em;
  padding-left: 1em;
  border-left: 3px solid #999;
  font-style: italic;
  color: #333;
}

/* Liste */
ul, ol {
  margin: 0.5em 0;
  padding-left: 1.5em;
}

li {
  margin: 0.3em 0;
}

/* Codice inline */
code, pre {
  font-family: "Courier New", "Courier", monospace;
  font-size: 0.9em;
}

pre {
  background-color: #f0f0f0;
  padding: 0.5em;
  border: 1px solid #ddd;
  white-space: pre-wrap;
  page-break-inside: avoid;
}

/* Link */
a {
  color: #222;
  text-decoration: underline;
}

/* Capitolo — pagina di inizio */
.chapter-title {
  text-align: center;
  margin: 2em 0 1.5em 0;
  font-size: 1.6em;
  font-weight: 700;
  page-break-before: always;
}

/* Nessun elemento interattivo */
* {
  transition: none !important;
  animation: none !important;
}
"""


__all__ = ["EINK_CSS"]
