---
description: Prepare a past assignment from the previous semester as supplementary material
---

# Prepare Past Assignment

This workflow prepares previous semester's assignments as supplementary material for students by generating a PDF containing the assignment instructions and highlighted Python solutions.

## Prerequisites
- The target week number (2--14) to prepare the assignments for.

## Steps

### 1. Analyze Intended Week
- Analyze the user's intention safely to determine the specific week (i.e., one of weeks 2--14).

### 2. Draft Reference Solutions
- Prepare a markdown file containing the problem descriptions and solutions for the targeted past assignment week. Ensure that the text ends with plain/imperative forms like `~작성하라.` (for student instructions) or `~출력한다.` and `~주어진다.` (for descriptions) rather than polite forms (`~합니다`, `~하시오` 등).
- Write and verify a Python script for every assignment to provide the reference solution and use it across the problem.

### 3. Generate LaTeX Source
- Create a LaTeX source file to build a PDF document.
- The output file must be saved as `assignments/solutions_spring25/week_X.tex` where X is the target week number.
- **Header:** Start the document with `\begin{center}` explicitly right after `\begin{document}` (do not use `titlepage`). Include the course name "프로그래밍입문", English course name "Introduction to Computer Programming (21102524)" on one `\Large` line, the semester (e.g., "Spring 2025") on the next `\Large` line, the specific week number as "Week X solution", Professor's name, department, university, and the SMU logo `\includegraphics[height=2.5cm]{smu_logo.pdf}`.
- **Sectioning:** Insert `\newpage` right before the start of each assignment (i.e. `\section*{...}`) starting from the second assignment (e.g. Assignment 2-2) so that each is placed on a separate page. Ensure that these assignment sections explicitly use an em-dash (`---`) rather than a colon for the title (e.g., `\section*{Assignment 2-1---[Title]}`).
- **Fonts:** Keep English and Math fonts as `TeX Gyre Pagella` (via `fontspec` and `unicode-math`). Set the main Korean font using `\setmainhangulfont{Apple SD Gothic Neo}` via `kotex`. 
- **Colors & Theme:** Import the `xcolor` and `tcolorbox` packages. Define custom colors for code syntax highlighting (e.g., `codegreen`, `codepurple`, `lightgrey`, `textpink`) and use a custom `\newtcblisting{codeblock}` environment. Make sure Korean comments in Python blocks are highlighted using `escapeinside={(*@}{@*)}` and wrapped in `(*@\textcolor{codegreen}{\# comment}@*)`. Also explicitly define `\newcommand{\ibox}[1]{\textcolor{blue}{\OldTexttt{#1}}}` and `\newcommand{\obox}[1]{\OldTexttt{#1}}` at the top of the file.
- **Input/Output format:** Define a `\newtcolorbox{examplebox}` macro with a gray border and sharp corners. Combine the Example Input and Example Output into a single block called `\begin{examplebox}{예제 입출력 X (파란색은 사용자 입력)}`. Output system text using `\obox{...}` and output user input (which should be blue) using `\ibox{...}`. For spacing without losing empty spaces, use `~` (e.g., `\obox{Enter a 3-digit number:~}`). Include formulas inline when needed.

### 4. Compile LaTeX
// turbo-all
- Compile the generated LaTeX source file into a PDF in the `assignments/solutions_spring25/` directory using `xelatex -shell-escape week_X.tex` (so fontspec and polyglossia support the custom OS fonts).
- LaTeX is assumed to be installed on the system.
