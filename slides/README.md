# BrightNest slides — COMP6708 Group 15

## Build

```powershell
cd slides
pdflatex -interaction=nonstopmode main.tex
pdflatex -interaction=nonstopmode main.tex
Copy-Item -Force main.pdf Assignment3-Group15.pdf
```

**Submit as:** `Assignment3-Group15.pdf`

## Design notes

- College Beamer · PolyU theme (maroon)
- Title background: `src/PolyU/title-bg-clean.jpg` (no campus building)
- COMP mark: `gallery/polyu-comp-logo.png`
- Script for the live 5 minutes: `../docs/demo-script.md`
