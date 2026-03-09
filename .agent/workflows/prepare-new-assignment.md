---
description: Prepare a new assignment for the current semester similar to a past assignment
---

# Prepare New Assignment

This workflow creates a new assignment for the current semester that maintains a similar difficulty level to a specified previous assignment.

## Steps

### 1. Analyze Previous Assignment (Optional)
- Review the past assignment's concepts and difficulty level if provided.

### 2. Draft New Assignment
- Prepare a very similar assignment in terms of difficulty level to the previous ones.
- The new assignment MUST be written in Korean. All sentence endings for instructions to students MUST use the imperative form `~작성하라.`. Other explanations MUST use the plain form (e.g., `~주어진다.`, `~출력한다.`) instead of polite forms (`~합니다.`, `~작성하시오.` 등).
- Insert `\newpage` right before the start of each assignment (i.e. `\section*{...}`) starting from the second assignment (e.g. Assignment 2-2) so they are placed on separated pages.
- Ensure that the assignment sections are titled specifically using an em-dash (`---`) instead of a colon (e.g., `\section*{Assignment 2-1---[Title]}`).

### 3. Develop & Verify Reference Solution
- Write a reference Python solution for each drafted assignment.
- Create tests (or run them manually in a terminal) to feed the generated "예제 입력" (Example Input) into the solution.
- Verify that the actual output exactly matches the drafted "예제 출력" (Example Output). Adjust the drafted assignment text if there are any discrepancies.

### 4. Generate LaTeX Source
- Create a LaTeX source file to build a PDF document.
- The output file must be saved as `assignments/solutions_spring26/week_X.tex` where X is the target week number.
- **Header:** Start the document with `\begin{center}` explicitly right after `\begin{document}` (do not use `titlepage`). Include the course name "프로그래밍입문", English course name "Introduction to Computer Programming (21102524)" on one `\Large` line, the semester (e.g., "Spring 2026") on the next `\Large` line, the specific week number as "Week X solution", Professor's name, department, university, and the SMU logo `\includegraphics[height=2.5cm]{smu_logo.pdf}`. (NOTE: adjust the path to the logo if necessary).
- **Fonts:** Keep English and Math fonts as `TeX Gyre Pagella` (via `fontspec` and `unicode-math`). Set the main Korean font using `\setmainhangulfont{Apple SD Gothic Neo}` via `kotex`. 
- **Colors & Theme:** Import the `xcolor` and `tcolorbox`, and define custom colors (e.g., `codegreen`, `codepurple`, `lightgrey`, `textpink`). Include the solution using the custom `\newtcblisting{codeblock}` environment, correctly escaping Korean comments with `escapeinside={(*@}{@*)}` and wrapping inside `(*@\textcolor{codegreen}{\# comment}@*)`. Also explicitly define `\newcommand{\ibox}[1]{\textcolor{blue}{\OldTexttt{#1}}}` and `\newcommand{\obox}[1]{\OldTexttt{#1}}` at the top of the file.
- **Input/Output format:** Define the `\newtcolorbox{examplebox}` macro with a gray border and sharp corners. Combine the Example Input and Example Output into a single block called `\begin{examplebox}{예제 입출력 X (파란색은 사용자 입력)}`. Output system text using `\obox{...}` and output user input (which should be blue) using `\ibox{...}`. For spacing without losing empty spaces, use `~` (e.g., `\obox{Enter a 3-digit number:~}`).

### 5. Compile LaTeX
// turbo-all
- Compile the generated LaTeX source file into a PDF in the `assignments/solutions_spring26/` directory using `xelatex -shell-escape week_X.tex` (so fontspec and polyglossia support the custom OS fonts).
- LaTeX is assumed to be installed on the system.
