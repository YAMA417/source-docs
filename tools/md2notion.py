import re, sys, pathlib

def split_row(line):
    s = line.strip()
    if s.startswith('|'): s = s[1:]
    if s.endswith('|'): s = s[:-1]
    # エスケープされていない | で分割
    cells, buf, i = [], '', 0
    while i < len(s):
        c = s[i]
        if c == '\\' and i+1 < len(s):
            buf += s[i:i+2]; i += 2; continue
        if c == '|':
            cells.append(buf.strip()); buf = ''; i += 1; continue
        buf += c; i += 1
    cells.append(buf.strip())
    return cells

def is_sep(line):
    s = line.strip()
    return bool(re.fullmatch(r'\|?[\s:|-]+\|?', s)) and '-' in s and '|' in s

def convert(text):
    lines = text.split('\n')
    out, i, in_code = [], 0, False
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith('```'):
            in_code = not in_code
            out.append(line); i += 1; continue
        if in_code:
            out.append(line); i += 1; continue
        # テーブル検出: | で始まり、次行が区切り
        if line.strip().startswith('|') and i+1 < len(lines) and is_sep(lines[i+1]):
            header = split_row(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                rows.append(split_row(lines[i])); i += 1
            out.append('<table header-row="true">')
            for r in [header] + rows:
                out.append('<tr>')
                for c in r:
                    out.append('<td>%s</td>' % c)
                out.append('</tr>')
            out.append('</table>')
            continue
        out.append(line); i += 1
    return '\n'.join(out)

src = pathlib.Path(sys.argv[1])
dst = pathlib.Path(sys.argv[2])
t = src.read_text(encoding='utf-8')
# 先頭の h1（ページタイトル）を除去
t = re.sub(r'\A#\s+[^\n]*\n+', '', t)
dst.write_text(convert(t), encoding='utf-8')
print(f'{src.name} -> {dst.name}')
