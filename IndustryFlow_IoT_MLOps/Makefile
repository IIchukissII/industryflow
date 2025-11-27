# Makefile для компіляції LaTeX документу

.PHONY: all clean view

# Назва головного файлу без розширення
MAIN = main

# Компілятор LaTeX
LATEX = pdflatex
BIBTEX = bibtex

all: $(MAIN).pdf

$(MAIN).pdf: $(MAIN).tex sections/*.tex references.bib
	$(LATEX) $(MAIN).tex
	$(BIBTEX) $(MAIN)
	$(LATEX) $(MAIN).tex
	$(LATEX) $(MAIN).tex

clean:
	rm -f *.aux *.log *.out *.toc *.bbl *.blg *.lof *.lot
	rm -f sections/*.aux

view: $(MAIN).pdf
	xdg-open $(MAIN).pdf 2>/dev/null || open $(MAIN).pdf || start $(MAIN).pdf

help:
	@echo "Доступні команди:"
	@echo "  make         - скомпілювати PDF"
	@echo "  make clean   - видалити тимчасові файли"
	@echo "  make view    - відкрити PDF"
	@echo "  make help    - показати цю довідку"
