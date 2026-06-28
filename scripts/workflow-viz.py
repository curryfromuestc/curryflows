#!/usr/bin/env python3
"""
workflow-viz.py -- render a curryflows Workflow JS template as a self-contained
HTML flowchart (SVG nodes + directional edges; no runtime dependency).

It STATICALLY parses the constrained Workflow DSL (export const meta; phase();
agent(prompt, opts); parallel([...]); pipeline(...); while-loop). A character
scanner tracks string / template-literal / comment state so brace/paren matching
ignores punctuation inside strings.

Rendered flow (top -> bottom, real edges):
  - fail-closed precheck gate (required contract/config fields, merged & cleaned)
  - produce lanes  (parallel agents fanned out side by side)
  - the bounded while-loop, drawn as a container with a back-edge (loop xN)
      validate -> cross-review panel (codex + Claude, per-lens fan-out) -> arbiter
  - HARD-STOP / archive gates, commit
Node colour = agentType (GP general-purpose writes / EX Explore read-only);
codex-leg and fan-out are badged; the prompt shows on hover.

Usage:
  workflow-viz.py <file.js> [-o out.html]
  workflow-viz.py <dir/>    [-o outdir/]     # all *.js + index.html
"""
import sys
import os
import re
import html
import argparse


# --------------------------------------------------------------------------- #
# 1. scanner: mark each char code (True) vs string/comment (False)
# --------------------------------------------------------------------------- #
def code_mask(src):
    n = len(src)
    mask = [True] * n
    i = 0
    while i < n:
        c = src[i]
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            while i < n and src[i] != '\n':
                mask[i] = False
                i += 1
            continue
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
        if c in "'\"":
            i = _skip_string(src, mask, i)
            continue
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
                            i = _skip_string(src, mask, i)
                            continue
                        if cc == '{':
                            depth += 1
                        elif cc == '}':
                            depth -= 1
                            if depth == 0:
                                mask[i] = False
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


def find_call_spans(src, mask, name):
    spans = []
    for m in re.finditer(r'\b' + re.escape(name) + r'\s*\(', src):
        if not mask[m.start()]:
            continue
        op = src.index('(', m.start())
        cp = match_balanced(src, mask, op, '(', ')')
        spans.append((m.start(), op, cp))
    return spans


def clean_template(text):
    text = text.strip()
    text = re.sub(r'\$\{([^{}]*)\}', r'{\1}', text)
    return text.strip('`\'"').strip()


def _interp_to_placeholder(text):
    """Render a ${expr} interpolation as a readable <name> placeholder for a
    tooltip, instead of leaving a broken-looking {expr}."""
    def repl(m):
        ids = re.findall(r'[A-Za-z_][A-Za-z0-9_]*', m.group(1))
        return '<' + (ids[-1] if ids else '…') + '>'
    return re.sub(r'\$\{([^{}]*)\}', repl, text)


# --------------------------------------------------------------------------- #
# 2. extraction
# --------------------------------------------------------------------------- #
def extract_meta(src, mask):
    name = ''
    desc = ''
    phases = []
    m = re.search(r'name\s*:\s*[\'"]([^\'"]+)[\'"]', src)
    if m:
        name = m.group(1)
    m = re.search(r"description\s*:\s*'([^']+)'", src) or \
        re.search(r'description\s*:\s*"([^"]+)"', src)
    if m:
        desc = m.group(1)
    pm = re.search(r'phases\s*:\s*\[', src)
    if pm:
        op = src.index('[', pm.start())
        cp = match_balanced(src, mask, op, '[', ']')
        phases = re.findall(r"title\s*:\s*'([^']+)'", src[op:cp])
    return {'name': name, 'description': desc, 'phases': phases}


def default_array_items(src, mask, ident):
    """Raw element strings of `const ident = ... || [ ... ]` (or None)."""
    m = re.search(r'\b' + re.escape(ident) + r'\s*=', src)
    if not m:
        return None
    b = src.find('[', m.end())
    if b < 0:
        return None
    cp = match_balanced(src, mask, b, '[', ']')
    commas = top_level_commas(src, mask, b + 1, cp)
    starts = [b + 1] + [c + 1 for c in commas]
    ends = [c for c in commas] + [cp]
    items = [src[s:e].strip() for s, e in zip(starts, ends) if src[s:e].strip()]
    return items


def loop_span(src, mask):
    wm = re.search(r'\bwhile\s*\(', src)
    if not wm or not mask[wm.start()]:
        return None
    cop = src.index('(', wm.start())
    ccp = match_balanced(src, mask, cop, '(', ')')
    brace = src.find('{', ccp)
    if brace < 0:
        return None
    return (brace, match_balanced(src, mask, brace, '{', '}'))


def extract_agents(src, mask):
    parallels = [(op, cp) for (_, op, cp) in find_call_spans(src, mask, 'parallel')]
    pipelines = [(op, cp) for (_, op, cp) in find_call_spans(src, mask, 'pipeline')]
    groups = parallels + pipelines
    agents = []
    for kw, op, cp in find_call_spans(src, mask, 'agent'):
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

        snippet = _interp_to_placeholder(prompt_expr)
        snippet = snippet.replace('\\n', ' ').replace('\\t', ' ').replace("\\'", "'")
        snippet = re.sub(r'`\s*\+\s*`', ' ', snippet)        # join concatenated templates
        snippet = snippet.replace('\\`', '`').replace('`', ' ')  # drop backticks
        snippet = re.sub(r'\s+', ' ', snippet).strip().strip('+').strip()
        if len(snippet) > 260:
            snippet = snippet[:260] + '…'

        pre = src[max(0, kw - 90):kw]
        fan = None
        fan_ident = None
        mm = re.search(r'(\w+)\.map\s*\(\s*\(?[^)]*\)?\s*=>\s*(?:\(\)\s*=>\s*)?$', pre)
        if mm and mm.group(1).isidentifier():
            fan_ident = mm.group(1)
            items = default_array_items(src, mask, fan_ident)
            fan = len(items) if items else 'N'

        group = None
        for (gop, gcp) in groups:
            if gop < kw < gcp and (group is None or gop > group[0]):
                group = (gop, gcp)

        agents.append({
            'pos': kw, 'label': label or '(agent)', 'phase': phase,
            'atype': atype, 'schema': schema, 'snippet': snippet,
            'codex': 'codex-review.sh' in prompt_expr,
            'fan': fan, 'fan_ident': fan_ident,
            'group': (group[0] if group else None),
        })
    return agents


def extract_gates(src, mask):
    gates = []
    pm = re.search(r"phase\(\s*'precheck'\s*\)", src)
    pstart = pend = -1
    if pm:
        pstart = pm.start()
        nxt = re.search(r"phase\(\s*'(?!precheck)\w+'\s*\)", src[pm.end():])
        pend = pm.end() + nxt.start() if nxt else len(src)
        fields = []
        for fm in re.finditer(r'for\s*\(\s*const\s+\w+\s+of\s*\[([^\]]+)\]', src[pstart:pend]):
            fields += re.findall(r"'([^']+)'", fm.group(1))
        cm = re.search(r"config requires ([^'\"`]+)", src[pstart:pend])
        text = ''
        if fields:
            text += 'contract: ' + ', '.join(fields)
        if cm:
            text += (' · ' if text else '') + 'config: ' + cm.group(1).strip()
        gates.append({'pos': pstart, 'kind': 'precheck', 'title': 'PRECHECK',
                      'text': text or 'fail-closed precheck', 'tip': text})

    for m in re.finditer(r'throw new Error\(', src):
        if not mask[m.start()] or (pm and pstart <= m.start() < pend):
            continue
        op = src.index('(', m.start())
        cp = match_balanced(src, mask, op, '(', ')')
        txt = re.sub(r'\s+', ' ', clean_template(src[op + 1:cp])).strip()
        txt = re.sub(r'\{\w+\}', '…', txt)
        gates.append({'pos': m.start(), 'kind': 'gate', 'title': 'GATE',
                      'text': txt[:130], 'tip': txt})

    for m in re.finditer(r'\bconst\s+archiveOk\s*=', src):
        if mask[m.start()]:
            gates.append({'pos': m.start(), 'kind': 'archive', 'title': 'ARCHIVE GATE',
                          'text': 'accepted · validation green · real diff · rollback · no fabrication',
                          'tip': 'archive gate (fail-closed)'})

    # HARD-STOP gate: only a real runtime marker `log('HARD-STOP: ...')`,
    # never a doc/inline comment (those would pollute the flow with header text).
    for m in re.finditer(r'\blog\s*\(', src):
        if not mask[m.start()]:
            continue
        op = src.index('(', m.start())
        cp = match_balanced(src, mask, op, '(', ')')
        inner = src[op + 1:cp]
        if 'HARD-STOP' not in inner:
            continue
        txt = re.sub(r'\s+', ' ', clean_template(inner)).strip()
        txt = re.sub(r'\{[^}]*\}', '…', txt)
        gates.append({'pos': m.start(), 'kind': 'hardstop', 'title': 'HARD-STOP',
                      'text': txt[:130], 'tip': txt})
    return gates


def _clean_label(lab):
    lab = re.sub(r':?r?\{round\}', '', lab)   # drop the per-round suffix :r{round}
    lab = re.sub(r'\{[^}]*\}', '', lab)        # any leftover template var
    return lab.strip().strip(':-· ').strip() or '(agent)'


def _expand(a, src, mask):
    """A fanned agent -> N render nodes (names resolved); else a single node."""
    base = {'kind': 'agent', 'atype': a['atype'], 'codex': a['codex'],
            'schema': a['schema'], 'tip': a['snippet'], 'hardstop': a.get('hardstop', False)}
    if a['fan'] in (None, 'N'):
        nd = dict(base)
        nd['label'] = _clean_label(a['label'])
        nd['fan'] = a['fan']
        return [nd]
    items = default_array_items(src, mask, a['fan_ident']) or []
    out = []
    for k in range(a['fan']):
        nd = dict(base)
        lab = a['label'].replace('{i}', str(k))
        name = None
        if k < len(items):
            it = items[k]
            nm = re.search(r"name\s*:\s*'([^']+)'", it)
            if nm:
                name = nm.group(1)
            elif it[:1] in "'\"`":
                nd['tip'] = clean_template(it)
        lab = re.sub(r'\{lane[^}]*\}', name if name else ('#' + str(k)), lab)
        nd['label'] = _clean_label(lab)
        nd['fan'] = None
        out.append(nd)
    return out


def build_model(path):
    with open(path, 'r', encoding='utf-8') as fh:
        src = fh.read()
    mask = code_mask(src)
    meta = extract_meta(src, mask)
    agents = extract_agents(src, mask)
    gates = extract_gates(src, mask)
    lp = loop_span(src, mask)
    max_rounds = 3
    mm = re.search(r'maxRounds\s*\|\|\s*(\d+)', src)
    if mm:
        max_rounds = int(mm.group(1))

    def looped(pos):
        return bool(lp and lp[0] < pos < lp[1])

    merged = [('a', a) for a in agents] + [('g', g) for g in gates]
    merged.sort(key=lambda x: x[1]['pos'])

    rows = []
    i = 0
    while i < len(merged):
        typ, obj = merged[i]
        if typ == 'g':
            rows.append({'kind': 'gate', 'nodes': [obj], 'looped': looped(obj['pos'])})
            i += 1
            continue
        a = obj
        if a['group'] is not None:
            grp = a['group']
            run = [a]
            j = i + 1
            while j < len(merged) and merged[j][0] == 'a' and merged[j][1]['group'] == grp:
                run.append(merged[j][1])
                j += 1
            nodes = []
            for x in run:
                nodes += _expand(x, src, mask)
            rows.append({'kind': 'parallel', 'nodes': nodes, 'looped': looped(a['pos'])})
            i = j
        else:
            nodes = _expand(a, src, mask)
            rows.append({'kind': ('parallel' if len(nodes) > 1 else 'single'),
                         'nodes': nodes, 'looped': looped(a['pos'])})
            i += 1
    return {'meta': meta, 'rows': rows, 'max_rounds': max_rounds,
            'file': os.path.basename(path)}


# --------------------------------------------------------------------------- #
# 3. SVG flowchart rendering (self-contained)
# --------------------------------------------------------------------------- #
# palettes -- light is an academic-paper look (white ground, warm accents);
# dark is the original. Tints are derived with color-mix so they track --bg.
THEMES = {
    # palette from autoDissectPaper (spdark/sphot/spblue/spann + red ramp)
    'light': ":root{--bg:#ffffff;--panel:#ffffff;--panel2:#f4f5f7;--ink:#374e55;--muted:#74808a;"
             "--line:#bfc6c9;--edge:#374e55;--gp:#df8f44;--ex:#2c5f8d;--gate:#a4161a;"
             "--hard:#a53e2a;--arch:#2c5f8d;--loop:#6a6599;--codex:#3f7d63;}",
    'dark': ":root{--bg:#0e1016;--panel:#1b1f2b;--panel2:#161a24;--ink:#e8ebf2;--muted:#98a2b6;"
            "--line:#3a4252;--edge:#5b6577;--gp:#f0a23b;--ex:#54a6f5;--gate:#e5556e;"
            "--hard:#ff476a;--arch:#54a6f5;--loop:#8a6bff;--codex:#23c8a4;}",
}

CSS_BASE = """
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:13px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:26px 18px 70px}
h1{font-size:19px;margin:0 0 3px}.sub{color:var(--muted);font-size:12px;margin:0 0 12px}
.desc{color:var(--muted);font-size:12px;margin:0 0 16px;padding:9px 12px;
background:var(--panel2);border:1px solid var(--line);border-radius:8px}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin:0 0 14px;font-size:11px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:5px}
.dot{width:10px;height:10px;border-radius:3px;display:inline-block}
.loopbox{fill:color-mix(in srgb,var(--loop) 6%,var(--bg));stroke:var(--loop)}
.card{height:100%;width:100%;background:var(--panel);border:1px solid var(--line);
border-left:4px solid var(--line);border-radius:9px;padding:7px 10px;overflow:hidden}
.card.gp{border-left-color:var(--gp)}.card.ex{border-left-color:var(--ex)}
.card .cl{font-weight:600;font-size:12.5px;display:flex;align-items:center;gap:6px;flex-wrap:wrap;line-height:1.3;color:var(--ink)}
.card .ct{color:var(--muted);font-size:11px;margin-top:3px;line-height:1.3}
.tag{font-size:9.5px;padding:1px 6px;border-radius:999px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.tag.gp{color:var(--gp);border-color:var(--gp)}.tag.ex{color:var(--ex);border-color:var(--ex)}
.tag.codex{color:var(--codex);border-color:var(--codex)}
.tag.fan{color:var(--loop);border-color:var(--loop)}
.tag.hard{color:var(--hard);border-color:var(--hard)}
.card.gate,.card.precheck{background:color-mix(in srgb,var(--gate) 9%,var(--bg));border:1px solid var(--gate);border-left-width:4px;border-left-color:var(--gate)}
.card.archive{background:color-mix(in srgb,var(--arch) 9%,var(--bg));border:1px solid var(--arch);border-left-width:4px;border-left-color:var(--arch)}
.card.hardstop{background:color-mix(in srgb,var(--hard) 12%,var(--bg));border:1px solid var(--hard);border-left-width:4px;border-left-color:var(--hard)}
.card.gate .cl,.card.precheck .cl{color:var(--gate)}.card.archive .cl{color:var(--arch)}.card.hardstop .cl{color:var(--hard)}
"""


def esc(s):
    return html.escape(s or '', quote=True)


def node_html(nd):
    if nd['kind'] in ('gate', 'precheck', 'archive', 'hardstop'):
        cls = nd['kind'] if nd['kind'] in ('precheck', 'archive', 'hardstop') else 'gate'
        return ('<div xmlns="http://www.w3.org/1999/xhtml" class="card %s" title="%s">'
                '<div class="cl">%s</div><div class="ct">%s</div></div>'
                ) % (cls, esc(nd['tip']), esc(nd['title']), esc(nd['text']))
    cls = 'gp' if nd['atype'] == 'general-purpose' else ('ex' if nd['atype'] == 'Explore' else '')
    tags = []
    if nd['atype'] == 'general-purpose':
        tags.append('<span class="tag gp">GP</span>')
    elif nd['atype'] == 'Explore':
        tags.append('<span class="tag ex">EX</span>')
    if nd['codex']:
        tags.append('<span class="tag codex">codex</span>')
    if nd.get('fan') == 'N':
        tags.append('<span class="tag fan">×N</span>')
    if nd.get('hardstop'):
        tags.append('<span class="tag hard">HARD-STOP</span>')
    schema = ('<div class="ct">→ %s</div>' % esc(nd['schema'])) if nd['schema'] else ''
    return ('<div xmlns="http://www.w3.org/1999/xhtml" class="card %s" title="%s">'
            '<div class="cl">%s %s</div>%s</div>'
            ) % (cls, esc(nd['tip']), esc(nd['label']), ' '.join(tags), schema)


def render(model, theme='light'):
    rows = model['rows']
    meta = model['meta']
    NODE_W, HGAP, MARGIN, RPAD = 200, 16, 28, 64
    AH, GH, VGAP = 86, 54, 44
    kmax = max([len(r['nodes']) for r in rows] + [1])
    CW = max(460, kmax * NODE_W + (kmax - 1) * HGAP)
    GW = min(560, CW)
    W = MARGIN + CW + RPAD + MARGIN
    cx = MARGIN + CW / 2.0

    # lay out rows top -> bottom
    laid = []
    y = 20.0
    for r in rows:
        n = len(r['nodes'])
        isgate = r['kind'] == 'gate'
        if isgate:
            h = 78 if len(r['nodes'][0].get('text', '')) > 64 else GH
        else:
            h = AH
        boxes = []
        if isgate or (r['kind'] == 'single' and n == 1):
            w = GW if isgate else NODE_W
            boxes.append((cx - w / 2.0, w, r['nodes'][0]))
        else:
            total = n * NODE_W + (n - 1) * HGAP
            sx = cx - total / 2.0
            for k, nd in enumerate(r['nodes']):
                boxes.append((sx + k * (NODE_W + HGAP), NODE_W, nd))
        laid.append({'y': y, 'h': h, 'boxes': boxes, 'looped': r['looped']})
        y += h + VGAP
    H = y + 8

    svg = []
    svg.append('<svg width="%d" height="%d" viewBox="0 0 %d %d" '
               'xmlns="http://www.w3.org/2000/svg" font-family="inherit">' % (W, int(H), W, int(H)))
    svg.append('<defs>'
               '<marker id="ah" markerWidth="9" markerHeight="9" refX="6.5" refY="3" '
               'orient="auto" markerUnits="userSpaceOnUse">'
               '<path d="M0,0 L7,3 L0,6 Z" fill="var(--edge)"/></marker>'
               '<marker id="ahl" markerWidth="10" markerHeight="10" refX="7" refY="3.2" '
               'orient="auto" markerUnits="userSpaceOnUse">'
               '<path d="M0,0 L7.5,3.2 L0,6.4 Z" fill="var(--loop)"/></marker></defs>')

    # loop container (behind nodes) + back-edge
    lidx = [i for i, r in enumerate(laid) if r['looped']]
    if lidx:
        f, l = min(lidx), max(lidx)
        ly0 = laid[f]['y'] - 16
        ly1 = laid[l]['y'] + laid[l]['h'] + 14
        svg.append('<rect class="loopbox" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="14" '
                   'stroke-width="1.4" stroke-dasharray="6 5"/>' % (MARGIN - 6, ly0, CW + 12, ly1 - ly0))
        svg.append('<text x="%.1f" y="%.1f" fill="var(--loop)" font-size="11" font-weight="700">'
                   '⟳ bounded loop ×%d max</text>' % (MARGIN + 4, ly0 + 14, model['max_rounds']))
        # back-edge in the right gutter: last loop row -> first loop row
        gx = MARGIN + CW + RPAD * 0.5
        last = laid[l]
        first = laid[f]
        lr = max(x + w for (x, w, _) in last['boxes'])
        fr = max(x + w for (x, w, _) in first['boxes'])
        ly = last['y'] + last['h'] / 2.0
        fy = first['y'] + first['h'] / 2.0
        svg.append('<path d="M %.1f %.1f L %.1f %.1f L %.1f %.1f L %.1f %.1f" '
                   'fill="none" stroke="var(--loop)" stroke-width="1.6" stroke-dasharray="5 4" '
                   'marker-end="url(#ahl)"/>' % (lr + 2, ly, gx, ly, gx, fy, fr + 4, fy))

    # sequential edges
    def anchor_bottom(row, box):
        x, w, _ = box
        return (x + w / 2.0, row['y'] + row['h'])

    def anchor_top(row, box):
        x, w, _ = box
        return (x + w / 2.0, row['y'])

    def edge(p1, p2):
        x1, y1 = p1
        x2, y2 = p2
        dy = (y2 - y1) * 0.45
        svg.append('<path d="M %.1f %.1f C %.1f %.1f %.1f %.1f %.1f %.1f" '
                   'fill="none" stroke="var(--edge)" stroke-width="1.5" '
                   'marker-end="url(#ah)"/>' % (x1, y1, x1, y1 + dy, x2, y2 - dy, x2, y2))

    for i in range(len(laid) - 1):
        A, B = laid[i], laid[i + 1]
        if len(A['boxes']) == 1:
            for b in B['boxes']:
                edge(anchor_bottom(A, A['boxes'][0]), anchor_top(B, b))
        elif len(B['boxes']) == 1:
            for a in A['boxes']:
                edge(anchor_bottom(A, a), anchor_top(B, B['boxes'][0]))
        else:
            for a, b in zip(A['boxes'], B['boxes']):
                edge(anchor_bottom(A, a), anchor_top(B, b))

    # nodes (foreignObject HTML cards on top)
    for row in laid:
        for (x, w, nd) in row['boxes']:
            svg.append('<foreignObject x="%.1f" y="%.1f" width="%.1f" height="%.1f">%s</foreignObject>'
                       % (x, row['y'], w, row['h'], node_html(nd)))
    svg.append('</svg>')

    head = []
    head.append('<div class="wrap">')
    head.append('<h1>curryflows · %s</h1>' % esc(meta['name'] or model['file']))
    head.append('<p class="sub">%s</p>' % esc(model['file']))
    if meta['description']:
        head.append('<p class="desc">%s</p>' % esc(meta['description']))
    head.append(
        '<div class="legend">'
        '<span><i class="dot" style="background:var(--gp)"></i>GP general-purpose · 改码/commit</span>'
        '<span><i class="dot" style="background:var(--ex)"></i>EX Explore · 只读评审</span>'
        '<span><i class="dot" style="background:var(--codex)"></i>codex 腿</span>'
        '<span><i class="dot" style="background:var(--loop)"></i>循环 / ×N 扇出</span>'
        '<span><i class="dot" style="background:var(--gate)"></i>fail-closed 门</span>'
        '</div>')
    head.append('<div style="overflow:auto">')
    head.append(''.join(svg))
    head.append('</div></div>')

    css = THEMES.get(theme, THEMES['light']) + CSS_BASE
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>curryflows · %s</title><style>%s</style></head><body>%s</body></html>'
            ) % (esc(meta['name'] or model['file']), css, ''.join(head))


def render_index(items, theme='light'):
    css = THEMES.get(theme, THEMES['light']) + CSS_BASE
    cards = []
    for fn, model in items:
        cards.append(
            '<a class="card ex" style="display:block;text-decoration:none;margin:10px 0;color:inherit;height:auto" href="%s">'
            '<div class="cl">%s</div><div class="ct">%s</div></a>'
            % (esc(fn), esc(model['meta']['name'] or fn),
               esc((model['meta']['description'] or '')[:170])))
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<title>curryflows workflows</title><style>%s</style></head>'
            '<body><div class="wrap"><h1>curryflows · workflows</h1>'
            '<p class="sub">%d 个模板</p>%s</div></body></html>'
            ) % (css, len(items), ''.join(cards))


def main():
    ap = argparse.ArgumentParser(description='Render a curryflows Workflow JS as an HTML flowchart.')
    ap.add_argument('path', help='a workflow .js file or a directory of them')
    ap.add_argument('-o', '--out', help='output .html (single) or dir (directory). '
                    'Default: <cwd>/.curryflows/diagrams/ -- a project runtime dir, '
                    'NOT the skill source tree.')
    ap.add_argument('--theme', choices=['light', 'dark'], default='light',
                    help='colour theme (default: light, academic-paper look)')
    args = ap.parse_args()
    # diagrams are a runtime artifact: default into the project's .curryflows/,
    # never into the skill source tree.
    default_dir = os.path.join(os.getcwd(), '.curryflows', 'diagrams')

    if os.path.isdir(args.path):
        outdir = args.out or default_dir
        os.makedirs(outdir, exist_ok=True)
        items = []
        for fn in sorted(os.listdir(args.path)):
            if not fn.endswith('.js'):
                continue
            model = build_model(os.path.join(args.path, fn))
            out = os.path.splitext(fn)[0] + '.html'
            with open(os.path.join(outdir, out), 'w', encoding='utf-8') as fh:
                fh.write(render(model, args.theme))
            items.append((out, model))
            print('wrote', os.path.join(outdir, out))
        if items:
            with open(os.path.join(outdir, 'index.html'), 'w', encoding='utf-8') as fh:
                fh.write(render_index(items, args.theme))
            print('wrote', os.path.join(outdir, 'index.html'))
    else:
        model = build_model(args.path)
        if args.out:
            out = args.out
        else:
            os.makedirs(default_dir, exist_ok=True)
            out = os.path.join(default_dir, os.path.splitext(os.path.basename(args.path))[0] + '.html')
        with open(out, 'w', encoding='utf-8') as fh:
            fh.write(render(model, args.theme))
        print('wrote', out)


if __name__ == '__main__':
    main()
