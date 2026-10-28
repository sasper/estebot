"""Server-rendered inline SVG charts.

Colours are referenced as CSS custom properties (--series-1 etc.) defined once in the
stylesheet, so light/dark swap in one place and the chart body is written against roles
rather than hex. Palette slots 1-3 of the validated default categorical theme.
"""
from datetime import date

def _nice(lo, hi, ticks=5):
    if hi == lo: hi = lo + 1
    span = hi - lo
    step = 10 ** (len(str(int(span))) - 1)
    for m in (1, 2, 2.5, 5, 10):
        if span / (step*m) <= ticks: step = step*m; break
    lo2 = (int(lo/step)) * step
    hi2 = (int(hi/step) + 1) * step
    return lo2, hi2, step

def line_chart(series, *, width=720, height=260, ylabel="", pad_l=52, pad_b=28,
               pad_t=14, pad_r=14, y_from_zero=False, fmt="{:.0f}", refs=(), dot=4.5):
    """series: [{"name":str, "points":[(date,value)], "slot":1..3, "dashed":bool}]
    refs: [(value, label)] - dashed reference lines (a baseline, a threshold) drawn in
    muted ink, so a threshold the verdict uses is visible on the chart it comes from."""
    pts_all = [v for s_ in series for (_, v) in s_["points"]]
    if not pts_all: return '<p class="muted">No data yet.</p>'
    pts_all = pts_all + [v for v, _ in refs]
    dates = sorted({dt for s_ in series for (dt, _) in s_["points"]})
    x0, x1 = dates[0], dates[-1]
    span_days = max((x1 - x0).days, 1)
    lo = 0 if y_from_zero else min(pts_all)
    lo, hi, step = _nice(lo, max(pts_all))
    W, H = width - pad_l - pad_r, height - pad_t - pad_b
    X = lambda dt: pad_l + (dt - x0).days / span_days * W
    Y = lambda v: pad_t + H - (v - lo) / (hi - lo) * H

    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" '
           f'aria-label="{ylabel or series[0]["name"]}">']
    # recessive gridlines + y axis labels
    t = lo
    while t <= hi + 1e-9:
        y = Y(t)
        out.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{pad_l-8}" y="{y+4:.1f}" text-anchor="end">{fmt.format(t)}</text>')
        t += step
    for v, label in refs:
        y = Y(v)
        out.append(f'<line class="ref" x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}"/>')
    # x labels: first, middle, last
    # the last one is right-aligned, or it runs past the edge of the chart
    for dt, anchor in ((x0, "middle"), (dates[len(dates)//2], "middle"), (x1, "end")):
        out.append(f'<text class="tick" x="{X(dt):.1f}" y="{height-8}" text-anchor="{anchor}">'
                   f'{dt.strftime("%-d %b")}</text>')
    ends = []
    for s_ in series:
        p = sorted(s_["points"])
        if not p: continue
        dash = ' stroke-dasharray="5 4"' if s_.get("dashed") else ""
        dpath = " ".join(f'{"M" if i==0 else "L"}{X(dt):.1f},{Y(v):.1f}'
                         for i, (dt, v) in enumerate(p))
        out.append(f'<path class="ln s{s_["slot"]}" d="{dpath}"{dash}/>')
        if not s_.get("dashed"):
            for dt, v in p:
                out.append(f'<circle class="pt s{s_["slot"]}" cx="{X(dt):.1f}" cy="{Y(v):.1f}" r="{dot}">'
                           f'<title>{dt.strftime("%-d %b")}: {fmt.format(v)}</title></circle>')
        # selective direct label on the final point - never a number on every point
        dt, v = p[-1]
        ends.append([Y(v) - 8, min(X(dt) + 8, width - pad_r), s_["slot"], fmt.format(v)])
    # Two series that end close together (a weigh-in and its trend) printed one label on top
    # of the other; stack them a line apart instead.
    ends.sort()
    for prev, cur in zip(ends, ends[1:]):
        cur[0] = max(cur[0], prev[0] + 14)
    for y, x, slot, text in ends:
        out.append(f'<text class="endlabel s{slot}" x="{x:.1f}" y="{y:.1f}" '
                   f'text-anchor="end">{text}</text>')
    # reference labels last, so their halo sits over the data rather than under it
    for v, label in refs:
        out.append(f'<text class="reflabel" x="{pad_l+4}" y="{Y(v)-4:.1f}">{label}</text>')
    out.append("</svg>")
    return "\n".join(out)

def bar_chart(points, *, width=720, height=170, pad_l=52, pad_b=28, pad_t=12, pad_r=14,
              fmt="{:.0f}", slot=3, threshold=None):
    if not points: return '<p class="muted">No data yet.</p>'
    p = sorted(points)
    hi = max(v for _, v in p) or 1
    lo, hi, step = _nice(0, hi, 4)
    W, H = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(p)
    bw = max(2.0, W / n - 2)
    X = lambda i: pad_l + i * (W / n)
    Y = lambda v: pad_t + H - (v / hi) * H
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="daily load">']
    t = 0
    while t <= hi + 1e-9:
        y = Y(t)
        out.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{pad_l-8}" y="{y+4:.1f}" text-anchor="end">{fmt.format(t)}</text>')
        t += step
    for i, (dt, v) in enumerate(p):
        h = max(0.0, pad_t + H - Y(v))
        out.append(f'<rect class="bar s{slot}" x="{X(i):.1f}" y="{Y(v):.1f}" width="{bw:.1f}" '
                   f'height="{h:.1f}" rx="2"><title>{dt.strftime("%-d %b")}: {fmt.format(v)}</title></rect>')
    for dt, i in ((p[0][0], 0), (p[n//2][0], n//2), (p[-1][0], n-1)):
        out.append(f'<text class="tick" x="{X(i)+bw/2:.1f}" y="{height-8}" text-anchor="middle">'
                   f'{dt.strftime("%-d %b")}</text>')
    out.append("</svg>")
    return "\n".join(out)
