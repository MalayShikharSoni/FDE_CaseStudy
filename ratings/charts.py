"""
Small SVG charts for the report, drawn in plain Python so the site is fully
static (no chart library, no server).

Colours are CSS classes defined in the page template, so each chart follows the
light or dark theme. Every mark carries a `data-tip` that the page shows on hover.
"""
from html import escape

WIDTH = 720


def _tip(text):
    return f' data-tip="{escape(text, quote=True)}"'


def _svg(height, body, label):
    return (f'<svg viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{escape(label, quote=True)}" '
            f'class="chart">{body}</svg>')


def _round_right(x, y, w, h, r=4):
    """A bar segment with rounded corners on its right (data) end only."""
    r = min(r, w, h / 2)
    return (f"M{x:.1f},{y:.1f} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r:.1f} "
            f"a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z")


def _round_top(x, y, w, h, r=4):
    """A column with rounded corners on its top (data) end only."""
    r = min(r, h, w / 2)
    return (f"M{x:.1f},{y + h:.1f} v-{h - r:.1f} a{r},{r} 0 0 1 {r},-{r} h{w - 2 * r:.1f} "
            f"a{r},{r} 0 0 1 {r},{r} v{h - r:.1f} z")


def dot_plot(rows, lo, hi, ticks, label):
    """One dot per row on a shared scale. rows: (name, value, note, emphasised).
    Used for ratings, where the differences are small and a zero-based bar would hide them."""
    left, right, row_h, top = 190, 70, 30, 8
    plot_w = WIDTH - left - right
    height = top + row_h * len(rows) + 28
    x = lambda v: left + (v - lo) / (hi - lo) * plot_w
    out = []
    for t in ticks:
        out.append(f'<line class="c-grid" x1="{x(t):.1f}" y1="{top}" x2="{x(t):.1f}" y2="{height - 24}"/>')
        out.append(f'<text class="c-tick" x="{x(t):.1f}" y="{height - 8}" text-anchor="middle">{t:.2f}</text>')
    for i, (name, value, note, strong) in enumerate(rows):
        cy = top + row_h * i + row_h / 2
        cls = "c-s1" if strong else "c-ref"
        out.append(f'<text class="c-label" x="{left - 12}" y="{cy + 4:.1f}" text-anchor="end">{escape(name)}</text>')
        out.append(f'<line class="c-stem" x1="{left}" y1="{cy:.1f}" x2="{x(value):.1f}" y2="{cy:.1f}"/>')
        out.append(f'<circle class="{cls} c-ring" cx="{x(value):.1f}" cy="{cy:.1f}" r="6"'
                   f'{_tip(f"{name}: {value:.2f}. {note}")}/>')
        out.append(f'<text class="c-value" x="{x(value) + 12:.1f}" y="{cy + 4:.1f}">{value:.2f}</text>')
    return _svg(height, "".join(out), label)


def star_bar(pcts, label):
    """One 100% bar split by star rating, 1-star on the left."""
    left, bar_h, gap = 0, 28, 2
    classes = ["c-neg2", "c-neg1", "c-mid", "c-pos1", "c-pos2"]
    usable = WIDTH - gap * 4
    out, x = [], left
    for i, p in enumerate(pcts):
        w = usable * p / 100
        out.append(f'<rect class="{classes[i]}" x="{x:.1f}" y="4" width="{max(w, 1):.1f}" height="{bar_h}"'
                   f'{_tip(f"{i + 1}-star: {p}% of ratings")}/>')
        x += w + gap
    return _svg(bar_h + 8, "".join(out), label)


def line_chart(points, y_max, y_step, unit, label, reference=None):
    """A single line. points: (x_label, value, tip). Optional reference: (value, text)."""
    left, right, top, bottom = 44, 60, 14, 30
    plot_w, plot_h = WIDTH - left - right, 190
    height = top + plot_h + bottom
    n = len(points)
    x = lambda i: left + (i / (n - 1) * plot_w if n > 1 else plot_w / 2)
    y = lambda v: top + plot_h - v / y_max * plot_h
    out = []
    v = 0
    while v <= y_max + 1e-9:
        out.append(f'<line class="c-grid" x1="{left}" y1="{y(v):.1f}" x2="{left + plot_w}" y2="{y(v):.1f}"/>')
        out.append(f'<text class="c-tick" x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v:g}{unit}</text>')
        v += y_step
    if reference:
        ry = y(reference[0])
        out.append(f'<line class="c-refline" x1="{left}" y1="{ry:.1f}" x2="{left + plot_w}" y2="{ry:.1f}"/>')
        out.append(f'<text class="c-tick" x="{left + plot_w + 6}" y="{ry + 4:.1f}">{escape(reference[1])}</text>')
    step = max(1, n // 8)
    for i, (name, _, _) in enumerate(points):
        if i % step == 0:
            out.append(f'<text class="c-tick" x="{x(i):.1f}" y="{height - 8}" text-anchor="middle">{escape(name)}</text>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(p[1]):.1f}" for i, p in enumerate(points))
    out.append(f'<path class="c-line" d="{path}"/>')
    last = points[-1]
    out.append(f'<circle class="c-s1 c-ring" cx="{x(n - 1):.1f}" cy="{y(last[1]):.1f}" r="4"/>')
    if not reference:
        out.append(f'<text class="c-value" x="{x(n - 1) + 8:.1f}" y="{y(last[1]) + 4:.1f}">{last[1]:g}{unit}</text>')
    slot = plot_w / max(n - 1, 1)
    for i, (_, value, tip) in enumerate(points):   # wide invisible targets so hover is easy
        out.append(f'<rect class="c-hit" x="{x(i) - slot / 2:.1f}" y="{top}" width="{slot:.1f}" height="{plot_h}"'
                   f' data-dot="{x(i):.1f},{y(value):.1f}"{_tip(tip)}/>')
    out.append('<circle class="c-s1 c-ring c-hover-dot" r="5" cx="-20" cy="-20"/>')
    return _svg(height, "".join(out), label)


def stacked_bars(rows, keys, x_max, unit, label):
    """Horizontal stacked bars. rows: (name, {key: value}, tip_prefix). keys: (key, css_class, legend)."""
    left, right, row_h, bar_h, gap, top = 240, 60, 26, 14, 2, 6   # left fits the longest theme name
    plot_w = WIDTH - left - right
    height = top + row_h * len(rows) + 6
    out = []
    for i, (name, parts, total_text) in enumerate(rows):
        cy = top + row_h * i + row_h / 2
        out.append(f'<text class="c-label" x="{left - 12}" y="{cy + 4:.1f}" text-anchor="end">{escape(name)}</text>')
        x = left
        present = [(k, c, lg) for k, c, lg in keys if parts.get(k)]
        for j, (key, cls, legend) in enumerate(present):
            w = max(parts[key] / x_max * plot_w - (gap if j < len(present) - 1 else 0), 1.5)
            tip = _tip(f"{name}: {parts[key]:g}{unit} {legend}")
            if j == len(present) - 1:
                out.append(f'<path class="{cls}" d="{_round_right(x, cy - bar_h / 2, w, bar_h)}"{tip}/>')
            else:
                out.append(f'<rect class="{cls}" x="{x:.1f}" y="{cy - bar_h / 2:.1f}" width="{w:.1f}" height="{bar_h}"{tip}/>')
            x += w + gap
        out.append(f'<text class="c-value" x="{x + 6:.1f}" y="{cy + 4:.1f}">{escape(total_text)}</text>')
    return _svg(height, "".join(out), label)


def columns(rows, y_max, y_step, unit, label, reference=None):
    """Vertical columns. rows: (x_label, value, tip). Optional reference: (value, text)."""
    left, right, top, bottom = 44, 96, 14, 30
    plot_w, plot_h = WIDTH - left - right, 170
    height = top + plot_h + bottom
    n = len(rows)
    slot = plot_w / n
    col_w = min(24, slot - 2)
    y = lambda v: top + plot_h - v / y_max * plot_h
    out = []
    v = 0
    while v <= y_max + 1e-9:
        out.append(f'<line class="c-grid" x1="{left}" y1="{y(v):.1f}" x2="{left + plot_w}" y2="{y(v):.1f}"/>')
        out.append(f'<text class="c-tick" x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v:g}{unit}</text>')
        v += y_step
    step = max(1, n // 7)
    for i, (name, value, tip) in enumerate(rows):
        cx = left + slot * i + slot / 2
        h = value / y_max * plot_h
        out.append(f'<path class="c-s1" d="{_round_top(cx - col_w / 2, y(value), col_w, max(h, 1))}"{_tip(tip)}/>')
        if i % step == 0 or i == n - 1:
            out.append(f'<text class="c-tick" x="{cx:.1f}" y="{height - 8}" text-anchor="middle">{escape(name)}</text>')
    if reference:
        ry = y(reference[0])
        out.append(f'<line class="c-refline" x1="{left}" y1="{ry:.1f}" x2="{left + plot_w}" y2="{ry:.1f}"/>')
        out.append(f'<text class="c-tick" x="{left + plot_w + 6}" y="{ry + 4:.1f}">{escape(reference[1])}</text>')
    return _svg(height, "".join(out), label)
