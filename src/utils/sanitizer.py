
def sanitize_traceback(text: str) -> str:
    """
    Sanitizes traceback string to hide system/grader paths.
    Only allows paths containing '/submission/' or 'Target.py' or '<string>'.
    Filters out the file line and the following code line for other paths.
    """
    if not text:
        return ""

    lines = text.splitlines()
    filtered = []
    skip_next = False
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('File "'):
            try:
                path_part = stripped.split('"')[1]
                
                if "/submission/" in path_part or "Target.py" in path_part or "<string>" in path_part:
                    skip_next = False
                    filtered.append(line)
                else:
                    skip_next = True
                    continue 
            except IndexError:
                skip_next = False
                filtered.append(line)
                
        elif skip_next and line.startswith('    '):
            continue
            
        else:
            skip_next = False
            filtered.append(line)
            
    sanitized_trace = "\n".join(filtered).strip()
    return sanitized_trace

def sanitize_system_error(error_msg: str) -> tuple[str, bool]:
    """
    Translates raw system errors into student-friendly Korean comments.
    Returns a tuple: (sanitized_comment, should_alert_admin).

    Branches stay concrete enough that the student can act without contacting a TA.
    Only true infrastructure failures (Launcher/Grader/Parse) raise should_alert.
    """
    e = error_msg or ""

    if "Time Limit" in e or "Timed Out" in e:
        return ("\n(시간 초과: 무한 루프가 있거나 연산량이 너무 많습니다.)", False)
    if "Output Limit" in e:
        return ("\n(출력 한도 초과: 디버그 print를 제거하거나 반복문 안의 출력을 줄여주세요.)", False)
    if "Memory Limit" in e or "exit 137" in e or "OOM" in e:
        return ("\n(메모리 한도 초과: 자료구조 크기를 줄이거나 불필요한 누적을 제거해주세요.)", False)
    if "Launcher Crashed" in e or "Malformed Output" in e:
        return (f"\n(채점기 인프라 오류 — 학생 코드와 무관할 수 있습니다. 동일 코드를 한 번 더 제출해 보세요. 진단: {e[:160]})", True)
    if "Grader Crashed" in e or "Special Judge Output Format Error" in e:
        return (f"\n(채점기 그레이더 충돌 — 출제자에게 자동 알림 전송됨. 진단: {e[:160]})", True)
    if "Failed to parse" in e:
        return ("\n(채점 결과 파싱 오류 — 동일 코드를 한 번 더 제출해주세요.)", True)
    return (f"\n(분류되지 않은 채점 오류: {e[:200]})", True)
