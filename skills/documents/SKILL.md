# documents skill

Documents on the PC (plan §34 "Document read", §56 Document capability).

| Tool | Action (level) | What |
|---|---|---|
| `read` | file.read (GREEN) | text of .pdf .docx .xlsx .csv .txt .md — **untrusted data** |
| `create` | file.create (BLUE) | a report / checklist / summary as .docx, .md or .txt ('# heading', '- bullet', '1. item', '- [ ] todo') |
| `pdf_merge` | file.create (BLUE) | join PDFs into a new PDF |
| `pdf_split` | file.create (BLUE) | pages '1-3,5' into a new PDF |
| `sheet_create` | file.create (BLUE) | rows into a new .xlsx / .csv (first row = header) |

Every write makes a NEW file (never overwrites); paths follow the files skill's
policy. A scanned PDF has no text layer (OCR is not built).
