
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
            
    return "\n".join(filtered).strip()
