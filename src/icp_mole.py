import argparse
import logging
import os
import re
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

sys.path.append(str(Path(__file__).parent.parent))
load_dotenv()

from src.utils.mole_token import answer_token

app = FastAPI(title="ICP 2026 Mole Challenge")

logger = logging.getLogger("icp_mole.ua_filter")

BLOCKED_UA_PATTERNS: tuple[str, ...] = (
    # OpenAI
    "gptbot", "oai-searchbot", "chatgpt-user",
    # Anthropic
    "claudebot", "claude-user", "claude-searchbot", "claude-web", "anthropic-ai",
    # Perplexity
    "perplexitybot", "perplexity-user",
    # Google AI (Google-Extended는 robots.txt 토큰이라 UA로는 안 옴 → 제외)
    "google-notebooklm", "google-read-aloud", "google-cloudvertexbot", "googleother",
    # Amazon / Meta
    "amazonbot", "meta-externalagent", "facebookbot",
    # Apple (Applebot은 검색용이라 학습 거부용 Applebot-Extended만)
    "applebot-extended",
    # Common Crawl / Mistral / ByteDance / DuckDuckGo
    "ccbot", "mistralai-user", "bytespider", "duckassistbot",
    # 기타
    "youbot", "cohere-ai", "diffbot", "imagesiftbot",
    "webzio-extended", "omgilibot", "omgili", "timpibot",
)


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else "-"


@app.middleware("http")
async def block_ai_crawlers(request: Request, call_next):
    ua_raw = request.headers.get("user-agent", "")
    ua = ua_raw.lower()
    if ua:
        for pat in BLOCKED_UA_PATTERNS:
            if pat in ua:
                logger.info(
                    "Blocked AI crawler UA=%r IP=%s path=%s",
                    ua_raw[:200], _client_ip(request), request.url.path,
                )
                return Response(
                    "Automated AI crawlers are not permitted on this page.",
                    status_code=403,
                    media_type="text/plain; charset=utf-8",
                )
    return await call_next(request)


class TokenIn(BaseModel):
    personal: str


@app.post("/token")
async def make_answer(payload: TokenIn):
    p = payload.personal.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", p):
        raise HTTPException(400, "개인 토큰은 32자리 16진수여야 합니다.")
    return JSONResponse({"answer": answer_token(p)})


PAGE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>두더지 챌린지 — Assignment 13</title>
<style>
  :root {
    --blue: #003087;
    --hot: #d61f5a;
    --paper: #f8f6f1;
    --ink: #1c1c1c;
    --muted: #6b6b6b;
    --line: #d8d2c2;
    --cell: #ffffff;
    --cell-active: #1e6bff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
    background: var(--paper);
    color: var(--ink);
    line-height: 1.55;
  }
  header {
    background: var(--blue);
    color: #fff;
    padding: 16px 24px;
  }
  header h1 { margin: 0; font-size: 18px; }
  header .sub { font-size: 12px; opacity: 0.8; margin-top: 2px; }
  main {
    max-width: 720px;
    margin: 32px auto;
    padding: 0 20px;
  }
  h2 { color: var(--blue); margin-top: 0; }
  code, kbd {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    background: #eee5cf;
    padding: 1px 6px;
    border-radius: 3px;
  }
  .card {
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 22px 26px;
    margin: 18px 0;
  }
  .muted { color: var(--muted); font-size: 14px; }

  .status {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 14px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .status .item { font-size: 16px; }
  .status .item b { color: var(--blue); font-size: 22px; }
  .status .item.time b.warn { color: var(--hot); }

  .board {
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 6px;
    aspect-ratio: 1 / 1;
    user-select: none;
  }
  .cell {
    background: var(--cell);
    border: 1px solid var(--line);
    border-radius: 4px;
    cursor: default;
    transition: background 70ms linear;
  }
  .cell.active {
    background: var(--cell-active);
    border-color: var(--cell-active);
    box-shadow: 0 0 0 2px rgba(30,107,255,0.25) inset;
    cursor: pointer;
  }

  button {
    background: var(--blue);
    color: #fff;
    border: 0;
    padding: 10px 18px;
    font-size: 15px;
    border-radius: 4px;
    cursor: pointer;
  }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  button.secondary {
    background: #fff;
    color: var(--blue);
    border: 1px solid var(--blue);
  }

  .verdict {
    margin-top: 14px;
    padding: 10px 14px;
    border-radius: 4px;
    font-weight: 600;
    display: none;
  }
  .verdict.ok    { display: block; background: #ecf6ee; border: 1px solid #2f7a3a; color: #2f7a3a; }
  .verdict.fail  { display: block; background: #fdecef; border: 1px solid var(--hot); color: var(--hot); }

  .token-box {
    margin-top: 18px;
    padding-top: 16px;
    border-top: 1px dashed var(--line);
    display: none;
  }
  .token-box.shown { display: block; }
  .token-box label { display: block; font-weight: 600; margin: 10px 0 6px; }
  .token-box input {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid var(--line);
    border-radius: 4px;
    font-size: 15px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .answer {
    background: #fffdf7;
    border: 1px dashed var(--line);
    padding: 14px;
    text-align: center;
    margin-top: 14px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    word-break: break-all;
    color: var(--hot);
    font-size: 15px;
    display: none;
  }
  .answer.shown { display: block; }

  footer {
    max-width: 720px;
    margin: 50px auto 24px;
    padding: 0 20px;
    text-align: center;
    color: var(--muted);
    font-size: 12px;
  }
</style>
</head>
<body>
<header>
  <h1>두더지 챌린지 <span class="sub">— 프로그래밍입문 2026-1 / Assignment 13</span></h1>
</header>
<main>

<div class="card">
  <h2>규칙</h2>
  <ul>
    <li><strong>3초</strong> 안에 활성화된 칸(파란색)을 <strong>10번</strong> 클릭하면 통과합니다.</li>
    <li>활성 칸을 클릭하면 즉시 다른 칸으로 점프합니다. 비활성 칸은 클릭해도 무시됩니다.</li>
    <li>3초가 지나도 게임은 자동 종료되지 않습니다. 활성 칸 구조를 분석할 시간을 가지세요.</li>
    <li>10번 클릭이 끝나는 순간의 경과 시간이 3초를 넘으면 실패입니다. 시작 버튼을 다시 누르세요.</li>
    <li>통과 후 SnowBoard Assignment 13의 <em>개인 피드백 토큰</em>을 붙여넣어 <em>정답 토큰</em>을 생성하십시오.</li>
  </ul>
  <p class="muted">힌트: <code>elem.click()</code>. 코드는 손보다 빠르니까.</p>
</div>

<div class="card">
  <div class="status">
    <div class="item">점수&nbsp;<b id="score">0</b>&nbsp;/&nbsp;10</div>
    <div class="item time">경과 시간&nbsp;<b id="time">0.00</b>s</div>
    <div class="item"><button id="start">시작</button></div>
  </div>
  <div class="board" id="board"></div>
  <div class="verdict" id="verdict"></div>

  <div class="token-box" id="tokenBox">
    <label for="personal">개인 토큰 (32자리 16진수)</label>
    <input id="personal" type="text" autocomplete="off" maxlength="32"
           placeholder="예: e2fcc738438c4e7ca4b605ef8764db73">
    <div style="margin-top:12px;">
      <button id="genBtn">정답 토큰 생성</button>
    </div>
    <div class="answer" id="answer"></div>
  </div>
</div>

</main>
<footer>&copy; 2026 SNSec Lab. / Sookmyung Women&apos;s University</footer>

<script>
(() => {
  const N = 6;
  const TARGET = 10;
  const TIME_MS = 3000;

  const board = document.getElementById('board');
  const scoreEl = document.getElementById('score');
  const timeEl = document.getElementById('time');
  const startBtn = document.getElementById('start');
  const verdict = document.getElementById('verdict');
  const tokenBox = document.getElementById('tokenBox');
  const personal = document.getElementById('personal');
  const genBtn = document.getElementById('genBtn');
  const answer = document.getElementById('answer');

  const cells = [];
  for (let i = 0; i < N * N; i++) {
    const d = document.createElement('div');
    d.className = 'cell';
    d.dataset.idx = i;
    d.addEventListener('click', () => onClick(i));
    board.appendChild(d);
    cells.push(d);
  }

  let activeIdx = -1;
  let score = 0;
  let running = false;
  let startedAt = 0;
  let raf = null;

  function pickRandom(except) {
    let n;
    do { n = Math.floor(Math.random() * N * N); } while (n === except);
    return n;
  }

  function setActive(n) {
    if (activeIdx >= 0) cells[activeIdx].classList.remove('active');
    activeIdx = n;
    cells[activeIdx].classList.add('active');
  }

  function setVerdict(kind, text) {
    verdict.className = 'verdict ' + kind;
    verdict.textContent = text;
  }
  function clearVerdict() { verdict.className = 'verdict'; verdict.textContent = ''; }

  function onClick(i) {
    if (!running) return;
    if (i !== activeIdx) return;  // 비활성 클릭 무시
    score++;
    scoreEl.textContent = score;
    if (score >= TARGET) {
      const elapsed = performance.now() - startedAt;
      stop(elapsed <= TIME_MS);
    } else {
      setActive(pickRandom(activeIdx));
    }
  }

  function tick() {
    const elapsedMs = performance.now() - startedAt;
    const s = elapsedMs / 1000;
    timeEl.textContent = s.toFixed(2);
    if (s > 3.0) timeEl.classList.add('warn'); else timeEl.classList.remove('warn');
    raf = requestAnimationFrame(tick);
  }

  function start() {
    if (running) return;
    running = true;
    score = 0;
    scoreEl.textContent = '0';
    clearVerdict();
    tokenBox.classList.remove('shown');
    answer.classList.remove('shown');
    answer.textContent = '';
    setActive(pickRandom(-1));
    startedAt = performance.now();
    timeEl.textContent = '0.00';
    timeEl.classList.remove('warn');
    startBtn.disabled = true;
    raf = requestAnimationFrame(tick);
  }

  function stop(success) {
    running = false;
    if (raf) cancelAnimationFrame(raf);
    if (activeIdx >= 0) cells[activeIdx].classList.remove('active');
    activeIdx = -1;
    startBtn.disabled = false;
    startBtn.textContent = '다시 시작';
    const elapsed = (performance.now() - startedAt) / 1000;
    timeEl.textContent = elapsed.toFixed(2);
    if (success) {
      timeEl.classList.remove('warn');
      setVerdict('ok', `통과! ${TARGET}번 클릭 완료 (${elapsed.toFixed(2)}s) — 아래에서 개인 토큰을 입력하세요.`);
      tokenBox.classList.add('shown');
    } else {
      timeEl.classList.add('warn');
      setVerdict('fail', `실패. ${TARGET}번 클릭은 완료했지만 ${elapsed.toFixed(2)}s가 걸렸습니다 — 시작 버튼을 다시 누르세요.`);
    }
  }

  startBtn.addEventListener('click', start);

  genBtn.addEventListener('click', async () => {
    const p = personal.value.trim().toLowerCase();
    answer.classList.remove('shown');
    answer.style.color = '';
    if (!/^[0-9a-f]{32}$/.test(p)) {
      answer.textContent = '개인 토큰은 32자리 16진수여야 합니다.';
      answer.style.color = 'var(--hot)';
      answer.classList.add('shown');
      return;
    }
    genBtn.disabled = true;
    try {
      const res = await fetch('/token', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({personal: p}),
      });
      const data = await res.json();
      if (!res.ok) {
        answer.textContent = data.detail || '오류가 발생했습니다.';
        answer.style.color = 'var(--hot)';
      } else {
        answer.textContent = data.answer;
      }
      answer.classList.add('shown');
    } finally {
      genBtn.disabled = false;
    }
  });
})();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(PAGE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("src.icp_mole:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
