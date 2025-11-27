# IndustryFlow - Дипломна робота LaTeX Template

Шаблон для написання дипломної роботи згідно вимог Neoversity.

## Структура проєкту

```
thesis/
├── main.tex                    # Головний файл
├── references.bib              # Бібліографія
├── Makefile                    # Автоматизація компіляції
├── sections/                   # Розділи роботи
│   ├── 00_title.tex           # Титульна сторінка
│   ├── 01_certification.tex   # Сертифікація
│   ├── 02_declaration.tex     # Декларація
│   ├── 03_abstract_uk.tex     # Анотація (українською)
│   ├── 04_abstract_en.tex     # Анотація (англійською)
│   ├── 06_abbreviations.tex   # Список скорочень
│   ├── chapter01_introduction.tex      # Розділ 1: Вступ
│   ├── chapter02_literature_review.tex # Розділ 2: Огляд літератури
│   ├── chapter03_methodology.tex       # Розділ 3: Методологія
│   ├── chapter04_implementation.tex    # Розділ 4: Реалізація
│   ├── chapter05_results.tex          # Розділ 5: Результати
│   ├── chapter06_conclusions.tex      # Розділ 6: Висновки
│   ├── appendix_a.tex         # Додаток А: Діаграми
│   ├── appendix_b.tex         # Додаток Б: Код
│   └── appendix_c.tex         # Додаток В: Конфігурації
├── figures/                   # Рисунки та діаграми
└── tables/                    # Таблиці (якщо окремо)
```

## Вимоги

- **TeX Live** (повна інсталяція) або **MiKTeX**
- Пакети: babel-ukrainian, fontenc, geometry, та інші

### Встановлення на Ubuntu/Debian

```bash
sudo apt-get update
sudo apt-get install texlive-full texlive-lang-cyrillic
```

### Встановлення на macOS

```bash
brew install --cask mactex
```

### Встановлення на Windows

Завантажити MiKTeX: https://miktex.org/download

## Компіляція

### Використання Makefile (рекомендовано)

```bash
# Компіляція PDF
make

# Очистити тимчасові файли
make clean

# Відкрити PDF
make view
```

### Ручна компіляція

```bash
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

## Як писати

### 1. Персональні дані

Відредагуйте `sections/00_title.tex`:
- Змініть ім'я автора
- Вкажіть студентський номер
- Додайте ім'я керівника
- Вкажіть дату подання

### 2. Основні розділи

Редагуйте файли в `sections/`:
- `chapter01_introduction.tex` - Вступ
- `chapter02_literature_review.tex` - Огляд літератури
- `chapter03_methodology.tex` - Методологія
- `chapter04_implementation.tex` - Реалізація
- `chapter05_results.tex` - Результати
- `chapter06_conclusions.tex` - Висновки

### 3. Додавання рисунків

Помістіть зображення в `figures/`:

```latex
\begin{figure}[h]
\centering
\includegraphics[width=0.8\textwidth]{figures/my_diagram.png}
\caption{Опис рисунку}
\label{fig:my_diagram}
\end{figure}
```

Посилання в тексті: `\ref{fig:my_diagram}`

### 4. Додавання таблиць

```latex
\begin{table}[h]
\centering
\caption{Назва таблиці}
\label{tab:my_table}
\begin{tabular}{@{}lll@{}}
\toprule
\textbf{Колонка 1} & \textbf{Колонка 2} & \textbf{Колонка 3} \\ 
\midrule
Дані 1 & Дані 2 & Дані 3 \\
\bottomrule
\end{tabular}
\end{table}
```

### 5. Додавання формул

```latex
\begin{equation}
E = mc^2
\label{eq:einstein}
\end{equation}
```

Посилання: `\ref{eq:einstein}`

### 6. Додавання коду

```latex
\begin{lstlisting}[language=Python, caption={Приклад коду}]
def hello():
    print("Hello, World!")
\end{lstlisting}
```

### 7. Цитування літератури

В тексті:
```latex
Згідно дослідження \cite{liu2008isolation}, алгоритм...
```

Додайте джерела в `references.bib`:
```bibtex
@article{author2023,
  title={Назва статті},
  author={Прізвище, Ім'я},
  journal={Назва журналу},
  year={2023}
}
```

## Формат відповідно до вимог

✅ **Шрифт:** 12pt Times New Roman (основний текст)  
✅ **Міжрядковий інтервал:** 1.5  
✅ **Поля:** Ліве 30мм, праве 15мм, верхнє/нижнє 20мм  
✅ **Нумерація:** По центру нижнього колонтитула  
✅ **Заголовки розділів:** 14pt жирний, великі літери  
✅ **Заголовки підрозділів:** 12pt жирний  
✅ **Таблиці:** Нумерація X.Y, підпис зверху  
✅ **Рисунки:** Нумерація X.Y, підпис знизу  
✅ **Формули:** Нумерація (X.Y), вирівнювання по центру  

## TODO перед поданням

- [ ] Заповнити персональні дані в титульній сторінці
- [ ] Написати анотації (українською та англійською)
- [ ] Додати всі рисунки та діаграми
- [ ] Перевірити всі таблиці
- [ ] Додати всі цитування в references.bib
- [ ] Перевірити всі посилання \ref{}
- [ ] Додати антиплагіатний звіт (2 сторінки після декларації)
- [ ] Фінальна компіляція та перевірка PDF
- [ ] Отримати підписи керівника

## Корисні команди LaTeX

| Команда | Опис |
|---------|------|
| `\section{}` | Розділ |
| `\subsection{}` | Підрозділ |
| `\textbf{}` | Жирний текст |
| `\textit{}` | Курсив |
| `\cite{}` | Цитування |
| `\ref{}` | Посилання на рисунок/таблицю/формулу |
| `\label{}` | Мітка для посилання |
| `\newpage` | Нова сторінка |
| `\clearpage` | Нова сторінка + очистити floats |

## Підтримка

Якщо виникають проблеми з компіляцією:
1. Перевірте встановлені пакети
2. Очистіть тимчасові файли: `make clean`
3. Спробуйте скомпілювати вручну
4. Перевірте наявність всіх зображень в `figures/`

## Ліцензія

Цей шаблон вільний для використання студентами Neoversity.
