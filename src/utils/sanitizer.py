
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
        # Typical Traceback line: '  File "/path/to/file.py", line 10, in func'
        if stripped.startswith('File "'):
            try:
                # Extract path
                path_part = stripped.split('"')[1]
                
                # Whitelist: only show student code paths
                if "/submission/" in path_part or "Target.py" in path_part or "<string>" in path_part:
                    skip_next = False
                    filtered.append(line)
                else:
                    # System/Grader file -> Hide
                    skip_next = True
                    continue 
            except IndexError:
                # Malformed file line? Keep it to be safe
                skip_next = False
                filtered.append(line)
                
        elif skip_next and line.startswith('    '):
            # This is likely the source code line corresponding to the skipped stack frame
            # e.g. "    return func(*args)"
            # Skip it
            continue
            
        else:
            # Normal line (Traceback header, Error message, or student code snippet)
            skip_next = False
            filtered.append(line)
            
    sanitized_trace = "\n".join(filtered).strip()
    return sanitized_trace

def sanitize_system_error(error_msg: str) -> tuple[str, bool]:
    """
    Translates raw system errors into student-friendly Korean comments.
    Returns a tuple: (sanitized_comment, should_alert_admin)
    """
    if "Execution Timed Out" in error_msg:
        return " (시스템 안내: 실행 시간이 초과되었습니다. 무한 루프가 발생했거나 연산량이 너무 많지 않은지 확인해주세요.)", False
    
    # All other internal system errors (Malformed output, Docker crash, Hook errors, etc.)
    return " (시스템 오류가 발생했습니다. 담당 조교에게 문의해주세요.)", True
