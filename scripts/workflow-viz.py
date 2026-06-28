#!/usr/bin/env python3
"""
workflow-viz.py -- render a curryflows Workflow JS template as a self-contained
human-readable HTML diagram.

It STATICALLY parses the constrained Workflow DSL (export const meta; phase();
agent(prompt, opts); parallel([...]); pipeline(...); while-loop) -- no JS engine,
no npm dependency. A small character scanner tracks string / template-literal /
comment state so brace/paren matching ignores punctuation inside strings.

What it extracts per template:
  - meta.name / meta.description / meta.phases
  - every agent() call: label, phase, agentType (GP/EX), schema, codex-leg flag,
    a prompt snippet (hover tooltip), and `.map(...)` fan-out count (xN)
  - parallel([...]) groups  -> agents rendered side by side (concurrent)
  - the bounded while-loop   -> looped phases wrapped in a "loop xN" container
  - fail-closed gates (throw ... precheck) and the archive gate / HARD-STOPs

Usage:
  workflow-viz.py <file.js> [-o out.html]
  workflow-viz.py <dir/>    [-o outdir/]      # all *.js + an index.html
"""
import sys
import os
import re
import html
import argparse


# --------------------------------------------------------------------------- #
# 1. character scanner: mark each char as code (True) or string/comment (False)
# --------------------------------------------------------------------------- #
def code_mask(src):
    n = len(src)
    mask = [True] * n
    i = 0
    while i < n:
        c = src[i]
        # line comment
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            while i < n and src[i] != '\n':
                mask[i] = False
                i += 1
            continue
        # block comment
        if c == '/' and i + 1 < n and src[i + 1] == '*':
            mask[i] = False
            i += 1
            while i < n and not (src[i] == '*' and i + 1 < n and src[i + 1] == '/'):
                mask[i] = False
                i += 1
            if i < n:
                mask[i] = False
                i += 1
            if i < n:
                mask[i] = False
                i += 1
            continue
        # single / double quoted string
        if c == "'" or c == '"':
            q = c
            mask[i] = False
            i += 1
            while i < n and src[i] != q:
                if src[i] == '\\':
                    mask[i] = False
                    i += 1
                    if i < n:
                        mask[i] = False
                        i += 1
                    continue
                mask[i] = False
                i += 1
            if i < n:
                mask[i] = False
                i += 1
            continue
        # template literal (backticks) with ${...} interpolation (code inside)
        if c == '`':
            mask[i] = False
            i += 1
            while i < n and src[i] != '`':
                if src[i] == '\\':
                    mask[i] = False
                    i += 1
                    if i < n:
                        mask[i] = False
                        i += 1
                    continue
                if src[i] == '$' and i + 1 < n and src[i + 1] == '{':
                    mask[i] = False
                    mask[i + 1] = False
                    i += 2
                    depth = 1
                    while i < n and depth > 0:
                        cc = src[i]
                        if cc in "'\"`":
                            # skip a nested string inside the interpolation
                            i = _skip_string(src, mask, i)
                            continue
                        if cc == '{':
                            depth += 1
                        elif cc == '}':
                            depth -= 1
                            if depth == 0:
                                mask[i] = False  # closing } of ${...} is not code
                                i += 1
                                break
                        mask[i] = True
                        i += 1
                    continue
                mask[i] = False
                i += 1
            if i < n:
                mask[i] = False
                i += 1
            continue
        mask[i] = True
        i += 1
    return mask


def _skip_string(src, mask, i):
    n = len(src)
    q = src[i]
    mask[i] = False
    i += 1
    while i < n and src[i] != q:
        if src[i] == '\\':
            mask[i] = False
            i += 1
            if i < n:
                mask[i] = False
                i += 1
            continue
        mask[i] = False
        i += 1
    if i < n:
        mask[i] = False
        i += 1
    return i


def match_balanced(src, mask, open_idx, open_ch, close_ch):
    """Given src[open_idx]==open_ch (code), return index of the matching close."""
    n = len(src)
    depth = 0
    i = open_idx
    while i < n:
        if mask[i]:
            if src[i] == open_ch:
                depth += 1
            elif src[i] == close_ch:
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return n - 1


def top_level_commas(src, mask, start, end):
    """Indices of commas at depth 0 (ignoring (), [], {}) within (start,end)."""
    out = []
    depth = 0
    for i in range(start, end):
        if not mask[i]:
            continue
        ch = src[i]
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        elif ch == ',' and depth == 0:
            out.append(i)
    return out


# --------------------------------------------------------------------------- #
# 2. extraction
# --------------------------------------------------------------------------- #
def find_call_spans(src, mask, name):
    """All (kw_start, open_paren, close_paren) for `name(` at code positions."""
    spans = []
    for m in re.finditer(r'\b' + re.escape(name) + r'\s*\(', src):
        kw = m.start()
        if not mask[kw]:
            continue
        op = src.index('(', m.start())
        cp = match_balanced(src, mask, op, '(', ')')
        spans.append((kw, op, cp))
    return spans


def clean_template(text):
    text = text.strip()
    # turn ${expr} into {expr} for readability
    text = re.sub(r'\$\{([^}]*)\}', r'{\1}', text)
    text = text.strip('`\'"')
    return text


def extract_meta(src):
    name = ''
    desc = ''
    phases = []
    m = re.search(r'name\s*:\s*[\'"]([^\'"]+)[\'"]', src)
    if m:
        name = m.group(1)
    m = re.search(r"description\s*:\s*'([^']+)'", src)
    if not m:
        m = re.search(r'description\s*:\s*"([^"]+)"', src)
    if m:
        desc = m.group(1)
    pm = re.search(r'phases\s*:\s*\[', src)
    if pm:
        op = src.index('[', pm.start())
        cp = match_balanced(src, code_mask(src), op, '[', ']')
        block = src[op:cp]
        phases = re.findall(r"title\s*:\s*'([^']+)'", block)
    return {'name': name, 'description': desc, 'phases': phases}


def default_array_len(src, mask, ident):
    """Length of the default array literal of `const ident = ... || [ ... ]`."""
    m = re.search(r'\b' + re.escape(ident) + r'\s*=', src)
    if not m:
        return None
    bracket = src.find('[', m.end())
    if bracket < 0:
        return None
    # only accept the array if it's close after `||` (a default literal)
    seg = src[m.end():bracket]
    if '||' not in seg and ':' not in seg and seg.strip() not in ('', '['):
        # heuristic: still try, but bail if there is intervening logic
        pass
    cp = match_balanced(src, mask, bracket, '[', ']')
    inner_start = bracket + 1
    if not src[inner_start:cp].strip():
        return 0
    # count non-empty top-level segments (tolerates a trailing comma)
    commas = top_level_commas(src, mask, inner_start, cp)
    seg_starts = [inner_start] + [c + 1 for c in commas]
    seg_ends = [c for c in commas] + [cp]
    return sum(1 for s, e in zip(seg_starts, seg_ends) if src[s:e].strip())


def extract_agents(src, mask):
    agents = []
    parallels = find_call_spans(src, mask, 'parallel')
    pipelines = find_call_spans(src, mask, 'pipeline')
    groups = [(op, cp, 'parallel') for (_, op, cp) in parallels] + \
             [(op, cp, 'pipeline') for (_, op, cp) in pipelines]

    # while-loop body span
    loop_span = None
    wm = re.search(r'\bwhile\s*\(', src)
    if wm and mask[wm.start()]:
        cop = src.index('(', wm.start())
        ccp = match_balanced(src, mask, cop, '(', ')')
        brace = src.find('{', ccp)
        if brace >= 0:
            bend = match_balanced(src, mask, brace, '{', '}')
            loop_span = (brace, bend)

    for gid, (kw, op, cp) in enumerate(find_call_spans(src, mask, 'agent')):
        commas = top_level_commas(src, mask, op + 1, cp)
        first = commas[0] if commas else cp
        prompt_expr = src[op + 1:first]
        opts_expr = src[first + 1:cp] if commas else ''

        def opt(pat):
            mm = re.search(pat, opts_expr)
            return mm.group(1) if mm else ''

        label = opt(r'label\s*:\s*([`\'"][^`\'"]*[`\'"])')
        label = clean_template(label) if label else ''
        phase = opt(r"phase\s*:\s*'([^']+)'")
        atype = opt(r"agentType\s*:\s*'([^']+)'")
        schema = opt(r'schema\s*:\s*(\w+)')

        # prompt snippet (first readable chunk)
        snippet = prompt_expr
        snippet = re.sub(r'`\s*\+\s*\n?\s*`', ' ', snippet)   # join `..` + `..`
        snippet = clean_template(snippet)
        snippet = snippet.replace('\\n', ' ')
        snippet = re.sub(r'\s+', ' ', snippet).strip()
        if len(snippet) > 240:
            snippet = snippet[:240] + '...'

        is_codex_leg = 'codex-review.sh' in prompt_expr

        # fan-out: is this agent the body of an IDENT.map(...) ?
        pre = src[max(0, kw - 80):kw]
        fan = None
        mm = re.search(r'(\w+)\.map\s*\(\s*\(?[^)]*\)?\s*=>\s*(?:\(\)\s*=>\s*)?$', pre)
        if mm:
            ident = mm.group(1)
            fan = default_array_len(src, mask, ident) if ident.isidentifier() else None
            if fan is None:
                fan = 'N'

        group = None
        for (gop, gcp, kind) in groups:
            if gop < kw < gcp:
                if group is None or gop > group[0]:
                    group = (gop, gcp, kind)
        looped = bool(loop_span and loop_span[0] < kw < loop_span[1])

        agents.append({
            'pos': kw, 'label': label or '(agent)', 'phase': phase,
            'atype': atype, 'schema': schema, 'snippet': snippet,
            'codex': is_codex_leg, 'fanout': fan,
            'group': (group[0] if group else None),
            'group_kind': (group[2] if group else None),
            'looped': looped,
        })
    return agents, loop_span


def extract_gates(src, mask):
    """Fail-closed throws, archive gate, and HARD-STOP markers (pos, kind, text)."""
    gates = []
    for m in re.finditer(r'throw new Error\(', src):
        if not mask[m.start()]:
            continue
        op = src.index('(', m.start())
        cp = match_balanced(src, mask, op, '(', ')')
        txt = clean_template(src[op + 1:cp])
        txt = re.sub(r'\s+', ' ', txt).strip()
        kind = 'precheck' if ('precheck' in txt or 'fail-closed' in txt) else 'gate'
        gates.append({'pos': m.start(), 'kind': kind, 'text': txt[:160]})
    for m in re.finditer(r'\bconst\s+archiveOk\s*=', src):
        if mask[m.start()]:
            gates.append({'pos': m.start(), 'kind': 'archive',
                          'text': 'archive gate (fail-closed): accepted & validation green & real diff & rollback & no fabrication'})
    for m in re.finditer(r'HARD-STOP', src):
        # only the code-side markers (comments are fine too -- they flag intent)
        line_start = src.rfind('\n', 0, m.start()) + 1
        line_end = src.find('\n', m.start())
        line = src[line_start:line_end].strip().lstrip('/').strip()
        gates.append({'pos': m.start(), 'kind': 'hardstop', 'text': line[:160]})
    return gates


def build_model(path):
    with open(path, 'r', encoding='utf-8') as fh:
        src = fh.read()
    mask = code_mask(src)
    meta = extract_meta(src)
    agents, loop_span = extract_agents(src, mask)
    gates = extract_gates(src, mask)
    max_rounds = 3
    mm = re.search(r'maxRounds\s*\|\|\s*(\d+)', src)
    if mm:
        max_rounds = int(mm.group(1))
    nodes = [{'t': 'agent', 'pos': a['pos'], 'a': a} for a in agents]
    nodes += [{'t': 'gate', 'pos': g['pos'], 'g': g} for g in gates]
    nodes.sort(key=lambda x: x['pos'])
    return {'meta': meta, 'nodes': nodes, 'loop_span': loop_span,
            'max_rounds': max_rounds, 'file': os.path.basename(path)}


# --------------------------------------------------------------------------- #
# 3. HTML rendering (self-contained: inline CSS, no external assets)
# --------------------------------------------------------------------------- #
CSS = """
:root{--bg:#0f1117;--panel:#171a23;--ink:#e6e9ef;--muted:#9aa3b2;--line:#2a2f3a;
--gp:#f0a23b;--ex:#4ea1f0;--gate:#e5556e;--hard:#ff3b5c;--loop:#7c5cff;--codex:#27c0a0;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--muted);font-size:12px;margin:0 0 6px}
.desc{color:var(--muted);font-size:12.5px;margin:0 0 22px;padding:10px 12px;background:var(--panel);border:1px solid var(--line);border-radius:8px}
.flow{position:relative;padding-left:6px}
.phase{margin:0 0 6px}
.phase>.pname{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:14px 0 6px}
.row{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0}
.node{position:relative;background:var(--panel);border:1px solid var(--line);border-left-width:4px;
border-radius:10px;padding:9px 12px;min-width:200px;flex:1 1 220px;max-width:460px}
.node .lab{font-weight:600;font-size:13px;display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.node .meta{color:var(--muted);font-size:11px;margin-top:3px}
.node.gp{border-left-color:var(--gp)}.node.ex{border-left-color:var(--ex)}
.tag{font-size:10px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}
.tag.gp{color:var(--gp);border-color:var(--gp)}.tag.ex{color:var(--ex);border-color:var(--ex)}
.tag.codex{color:var(--codex);border-color:var(--codex)}
.tag.fan{color:var(--loop);border-color:var(--loop)}
.par{border:1px dashed var(--line);border-radius:12px;padding:8px 8px 2px;margin:6px 0;position:relative}
.par::before{content:"parallel · concurrent";position:absolute;top:-9px;left:12px;background:var(--bg);
padding:0 6px;font-size:10px;color:var(--muted)}
.gate{display:flex;align-items:center;gap:8px;margin:8px 0;padding:8px 12px;border-radius:8px;
background:rgba(229,85,110,.08);border:1px solid var(--gate);color:#ffd2da;font-size:12.5px}
.gate.archive{background:rgba(78,161,240,.08);border-color:var(--ex);color:#cfe6ff}
.gate.hard{background:rgba(255,59,92,.10);border-color:var(--hard);color:#ffd6dd;font-weight:600}
.gate .gi{font-weight:700;font-size:11px;letter-spacing:.05em}
.loop{border:1.5px solid var(--loop);border-radius:14px;padding:6px 12px 10px;margin:10px 0;
background:rgba(124,92,255,.05);position:relative}
.loop::before{content:"⟳ bounded loop · ×"attr(data-rounds)" max";position:absolute;top:-10px;left:14px;
background:var(--bg);padding:0 7px;font-size:11px;color:var(--loop);font-weight:600}
.arrow{height:14px;width:2px;background:var(--line);margin:0 0 0 24px}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0 4px;font-size:11px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:5px}
.dot{width:10px;height:10px;border-radius:3px;display:inline-block}
.tip{cursor:help;border-bottom:1px dotted var(--muted)}
a{color:var(--ex)}
"""


def esc(s):
    return html.escape(s or '', quote=True)


def render_node(a):
    cls = 'gp' if a['atype'] == 'general-purpose' else ('ex' if a['atype'] == 'Explore' else '')
    tags = []
    if a['atype'] == 'general-purpose':
        tags.append('<span class="tag gp">GP · writes</span>')
    elif a['atype'] == 'Explore':
        tags.append('<span class="tag ex">EX · read-only</span>')
    if a['codex']:
        tags.append('<span class="tag codex">codex leg</span>')
    if a['fanout'] is not None:
        tags.append('<span class="tag fan">×%s</span>' % esc(str(a['fanout'])))
    schema = ('<span class="meta">→ %s</span>' % esc(a['schema'])) if a['schema'] else ''
    return (
        '<div class="node %s" title="%s">'
        '<div class="lab">%s %s</div>'
        '<div class="meta tip">%s</div>%s</div>'
    ) % (cls, esc(a['snippet']), esc(a['label']), ' '.join(tags),
         esc(a['snippet'][:120]), schema)


def render(model):
    meta = model['meta']
    parts = []
    parts.append('<div class="wrap">')
    parts.append('<h1>curryflows · %s</h1>' % esc(meta['name'] or model['file']))
    parts.append('<p class="sub">%s</p>' % esc(model['file']))
    if meta['description']:
        parts.append('<p class="desc">%s</p>' % esc(meta['description']))
    parts.append(
        '<div class="legend">'
        '<span><i class="dot" style="background:var(--gp)"></i>GP general-purpose (改码/commit)</span>'
        '<span><i class="dot" style="background:var(--ex)"></i>EX Explore (只读评审)</span>'
        '<span><i class="dot" style="background:var(--codex)"></i>codex 腿</span>'
        '<span><i class="dot" style="background:var(--loop)"></i>×N 扇出 / 循环</span>'
        '<span><i class="dot" style="background:var(--gate)"></i>fail-closed 门</span>'
        '</div>')
    parts.append('<div class="flow">')

    nodes = model['nodes']
    loop_span = model['loop_span']
    in_loop = False
    cur_phase = None
    i = 0

    def close_phase():
        if cur_phase is not None:
            parts.append('</div>')  # .phase

    while i < len(nodes):
        nd = nodes[i]
        pos = nd['pos']
        # loop container open/close based on source span
        if loop_span and not in_loop and loop_span[0] < pos < loop_span[1]:
            close_phase()
            cur_phase = None
            parts.append('<div class="loop" data-rounds="%d">' % model['max_rounds'])
            in_loop = True
        if loop_span and in_loop and not (loop_span[0] < pos < loop_span[1]):
            close_phase()
            cur_phase = None
            parts.append('</div>')  # .loop
            in_loop = False

        if nd['t'] == 'gate':
            close_phase()
            cur_phase = None
            g = nd['g']
            gi = {'precheck': 'PRECHECK', 'archive': 'ARCHIVE GATE',
                  'hardstop': 'HARD-STOP', 'gate': 'GATE'}.get(g['kind'], 'GATE')
            cls = {'archive': 'archive', 'hardstop': 'hard'}.get(g['kind'], '')
            parts.append('<div class="gate %s"><span class="gi">%s</span>%s</div>'
                         % (cls, gi, esc(g['text'])))
            i += 1
            continue

        a = nd['a']
        ph = a['phase'] or '(unphased)'
        if ph != cur_phase:
            close_phase()
            parts.append('<div class="phase">')
            parts.append('<div class="pname">%s</div>' % esc(ph))
            cur_phase = ph

        # collect a run of consecutive agents sharing the same parallel group
        grp = a['group']
        if grp is not None:
            run = [a]
            j = i + 1
            while j < len(nodes) and nodes[j]['t'] == 'agent' and nodes[j]['a']['group'] == grp \
                    and (nodes[j]['a']['phase'] or '(unphased)') == ph:
                run.append(nodes[j]['a'])
                j += 1
            parts.append('<div class="par"><div class="row">')
            for x in run:
                parts.append(render_node(x))
            parts.append('</div></div>')
            i = j
        else:
            parts.append('<div class="row">%s</div>' % render_node(a))
            i += 1

    close_phase()
    if in_loop:
        parts.append('</div>')
    parts.append('</div>')  # .flow
    parts.append('</div>')  # .wrap

    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>curryflows · %s</title><style>%s</style></head><body>%s</body></html>'
            ) % (esc(meta['name'] or model['file']), CSS, ''.join(parts))


def render_index(items):
    cards = []
    for fn, model in items:
        cards.append(
            '<a class="node ex" style="display:block;text-decoration:none;margin:8px 0" href="%s">'
            '<div class="lab">%s</div><div class="meta">%s</div></a>'
            % (esc(fn), esc(model['meta']['name'] or fn),
               esc((model['meta']['description'] or '')[:160])))
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<title>curryflows workflows</title><style>%s</style></head>'
            '<body><div class="wrap"><h1>curryflows · workflows</h1>'
            '<p class="sub">%d 个模板</p>%s</div></body></html>'
            ) % (CSS, len(items), ''.join(cards))


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description='Render a curryflows Workflow JS as HTML.')
    ap.add_argument('path', help='a workflow .js file or a directory of them')
    ap.add_argument('-o', '--out', help='output .html file (single) or dir (directory input)')
    args = ap.parse_args()

    if os.path.isdir(args.path):
        outdir = args.out or os.path.join(args.path, 'diagrams')
        os.makedirs(outdir, exist_ok=True)
        items = []
        for fn in sorted(os.listdir(args.path)):
            if not fn.endswith('.js'):
                continue
            model = build_model(os.path.join(args.path, fn))
            out = os.path.splitext(fn)[0] + '.html'
            with open(os.path.join(outdir, out), 'w', encoding='utf-8') as fh:
                fh.write(render(model))
            items.append((out, model))
            print('wrote', os.path.join(outdir, out))
        if items:
            idx = os.path.join(outdir, 'index.html')
            with open(idx, 'w', encoding='utf-8') as fh:
                fh.write(render_index(items))
            print('wrote', idx)
    else:
        model = build_model(args.path)
        out = args.out or (os.path.splitext(args.path)[0] + '.html')
        with open(out, 'w', encoding='utf-8') as fh:
            fh.write(render(model))
        print('wrote', out)


if __name__ == '__main__':
    main()
