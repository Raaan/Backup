#!/usr/bin/env python3
"""Static source checks. Never execute business scripts or read server secrets."""
import ast
import pathlib
import re
root=pathlib.Path(__file__).resolve().parents[1]
count=0
for p in root.rglob('*.py'):
    if '.git' in p.parts:continue
    ast.parse(p.read_text(encoding='utf-8-sig'),filename=str(p));count+=1
for p in root.rglob('*'):
    if not p.is_file() or '.git' in p.parts:continue
    if p.suffix in ('.pem','.key','.db','.eml') or p.name in ('.msmtprc','server.private.json'):
        raise SystemExit('Private artifact in repository: '+str(p.relative_to(root)))
    if p.suffix=='.md':
        for target in re.findall(r'\]\(([^)]+)\)',p.read_text(encoding='utf-8-sig')):
            if not re.match(r'https?://',target) and not target.startswith('#'):
                if not (p.parent/target.split('#')[0]).exists():raise SystemExit('Broken link: '+target)
print('Source and repository checks passed; Python files:',count)
