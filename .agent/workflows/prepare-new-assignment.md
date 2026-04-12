---
description: Prepare a new assignment for the current semester similar to a past assignment
---

# Prepare New Assignment

This workflow creates a new assignment for the current semester that maintains a similar difficulty level to a specified previous assignment. It uses a multi-agent pipeline: **Draft -> Verify -> Review -> Fix -> Re-verify**.

## Phase 1: Analyze & Consult (Sequential)

### 1-1. Analyze Previous Assignment (Optional)
- Review the past assignment's concepts and difficulty level if provided.

### 1-2. Consult the User (Mandatory)
- Present the ideas or topics for the new assignment to the user and wait for their approval before proceeding to draft the actual assignment instructions and code.
- If the user provides feedback or asks for changes, incorporate them and ask for approval again.
- Agree on the number of problems (typically 5--6) and the target concepts.

## Phase 2: Draft (Parallel Agents)

Once the user approves the topics, spawn **one agent per problem** in parallel using the Agent tool. Each agent independently:

1. Writes the problem instruction in Korean following the style rules below.
2. Writes a reference Python solution as `assignments/solutions_spring26/weekX_N.py`.
3. Creates 2--3 example I/O pairs.

**Style Rules for All Agents:**
- All instructions MUST be written in Korean.
- Student instructions MUST use the imperative form `~작성하라.`.
- Descriptions MUST use the plain form (e.g., `~주어진다.`, `~출력한다.`) instead of polite forms.
- Each problem must include: Instruction, 입력 format, 출력 format, 2--3 example I/O, and a Solution.
- If the problem uses substring/partial matching, state it explicitly (e.g., "부분 문자열로서").
- If case-insensitivity or mixed-case input is involved, state it explicitly and include an example.
- Section titles use em-dash: `Assignment X-N---[Title]`.

Each agent writes its solution to `assignments/solutions_spring26/weekX_N.py` in a worktree to avoid conflicts.

## Phase 3: Verify Solutions (Parallel Agents)

After all drafts are collected, spawn **one agent per problem** in parallel. Each agent:

1. Reads the drafted solution file (`weekX_N.py`).
2. Runs **every** example input through the solution via `echo "INPUT" | conda run -n PythonJudgeSystem python weekX_N.py`.
3. Compares actual output against expected output **exactly** (character-by-character).
4. Reports: PASS/FAIL per example, with diff if FAIL.

If any solution fails, fix it before proceeding. Do **not** proceed to Phase 4 with broken solutions.

## Phase 4: Review Instructions (Parallel Agents)

Spawn **three review agents** in parallel. Each performs a different review perspective:

### Agent A -- Clarity & Completeness
- Is each instruction unambiguous? Could a student interpret it differently and write a valid but "wrong" solution?
- Are edge cases covered in examples (e.g., all-same values, empty results, boundary values)?
- Does every problem have at least 2 examples? If not, suggest additions.
- Are input/output formats clearly specified (data type, delimiter, terminator)?

### Agent B -- Solution & Example Consistency
- Re-run all solutions against all examples (redundant check after Phase 3).
- Verify that `print()` output format matches the expected output exactly (e.g., Python list `[1, 2, 3]` vs comma-separated `1, 2, 3`).
- Check that the solution uses only concepts appropriate for the target week (no advanced features students haven't learned yet).

### Agent C -- Difficulty & Pedagogy
- Is there a reasonable difficulty progression across problems?
- Are the problems sufficiently distinct (not just minor variations of each other)?
- Does the set cover the target concepts adequately?
- Are hints provided where slicing, modulo, or other non-obvious techniques are needed?

Each agent returns a structured report:
```
Problem X-N: [PASS / ISSUE]
  - Description of issue (if any)
  - Suggested fix
```

## Phase 5: Fix & Re-verify (Sequential then Parallel)

### 5-1. Consolidate Review (Sequential)
- Merge all three review reports.
- Categorize issues: **must-fix** (ambiguity, incorrect example, missing edge case) vs **nice-to-have** (extra example, wording polish).
- Present the must-fix items to the user for confirmation if any issue is debatable.

### 5-2. Apply Fixes (Sequential)
- Update the instruction text and solution files for all must-fix issues.
- Update example I/O pairs as needed.

### 5-3. Re-verify (Parallel Agents)
- Spawn one agent per **modified** problem to re-run all examples against the updated solution.
- Confirm all PASS. If any FAIL, loop back to 5-2 (max 2 iterations, then ask user).

## Phase 6: Generate LaTeX (Sequential)

Create a LaTeX source file at `assignments/solutions_spring26/week_X.tex`.

- **Header:** Start with `\begin{center}` right after `\begin{document}` (no `titlepage`). Include:
  - Course: "프로그래밍입문"
  - English: "Introduction to Computer Programming (21102524)" on one `\Large` line
  - Semester: "Spring 2026" on the next `\Large` line
  - "Week X solution"
  - Professor, department, university, SMU logo `\includegraphics[height=2.5cm]{smu_logo.pdf}`
- **Pagination:** Insert `\newpage` before each `\section*{...}` starting from the second problem.
- **Fonts:** English/Math: `TeX Gyre Pagella` via `fontspec`/`unicode-math`. Korean: `\setmainhangulfont{Apple SD Gothic Neo}` via `kotex`.
- **Colors & Theme:** Import `xcolor` and `tcolorbox`. Define `codegreen`, `codepurple`, `lightgrey`, `textpink`. Use `\newtcblisting{codeblock}` for solutions. Escape Korean comments with `escapeinside={(*@}{@*)}` wrapped in `(*@\textcolor{codegreen}{\# comment}@*)`. Define `\newcommand{\ibox}[1]{\textcolor{blue}{\OldTexttt{#1}}}` and `\newcommand{\obox}[1]{\OldTexttt{#1}}`.
- **Korean string literals in code blocks:** The `listings` package does NOT colorize multibyte (Korean) characters that appear inside Python string literals — they will render as plain black text instead of `codepurple`. To force the correct purple highlight, every Korean string literal must be wrapped using the escape pattern `(*@\textcolor{codepurple}{\mbox{'한글 문자열'}}@*)`. Important details:
  - Wrap the **entire** string literal **including** its surrounding quote characters inside `\mbox{...}` so that Korean glyphs are typeset correctly without lstlisting interference.
  - Inside the `\mbox{...}`, escape literal curly braces from `format()` placeholders as `\{` and `\}` (e.g., `'점수: {}점'` becomes `\mbox{'점수: \{\}점'}`).
  - Pure-ASCII string literals (e.g., `'YES'`, `'NO'`) do NOT need this treatment — `listings` colorizes them automatically.
  - Example: `print('최고 평균: {} {:.2f}점'.format(name, avg))` must be written as `print((*@\textcolor{codepurple}{\mbox{'최고 평균: \{\} \{:.2f\}점'}}@*).format(name, avg))` in the `.tex` source.
- **Example boxes:** `\newtcolorbox{examplebox}` with gray border, sharp corners. Title: `예제 입출력 X (파란색은 사용자 입력)`. User input via `\ibox{...}`, output via `\obox{...}`. Use `~` for trailing spaces.

## Phase 7: Compile & Final Check (Sequential)

// turbo-all

1. Compile: `cd assignments/solutions_spring26 && xelatex -shell-escape week_X.tex`
2. Verify PDF page count matches expected (1 title + N problems).
3. LaTeX is assumed to be installed on the system.
