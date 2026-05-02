#!/bin/bash
# Post-edit hook: fast syntax check for Python files (no Docker needed)
# Catches syntax errors immediately after Edit/Write, before test runs

FILE_PATH=$(python3 -c "import sys,json; print(json.load(sys.stdin).get('file_path',''))" <<< "$TOOL_INPUT" 2>/dev/null)

# Only check Python files that exist
[[ "$FILE_PATH" != *.py || ! -f "$FILE_PATH" ]] && exit 0

python3 -c "
import ast, sys
try:
    with open(sys.argv[1]) as f:
        ast.parse(f.read())
except SyntaxError as e:
    print(f'Syntax error at line {e.lineno}: {e.msg}')
    sys.exit(1)
" "$FILE_PATH"
