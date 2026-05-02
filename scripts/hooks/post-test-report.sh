#!/bin/bash
# Post-Bash hook: after pytest runs, summarize failures for the loom cycle
# Helps Claude stay oriented in RED vs GREEN phase

COMMAND=$(python3 -c "import sys,json; print(json.load(sys.stdin).get('command',''))" <<< "$TOOL_INPUT" 2>/dev/null)

# Only trigger on pytest commands
[[ "$COMMAND" != *pytest* ]] && exit 0

EXIT_CODE=$(python3 -c "
import sys, json
data = json.load(sys.stdin)
# Tool output might have exit_code or we check the output text
output = str(data)
if 'FAILED' in output or 'ERROR' in output:
    print('1')
else:
    print('0')
" <<< "$TOOL_OUTPUT" 2>/dev/null)

if [[ "$EXIT_CODE" == "1" ]]; then
    echo "LOOM: Tests failing — you should be in RED or fixing GREEN. Do not refactor until tests pass."
fi
