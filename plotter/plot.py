"""
plot.py — SVG → G-code plotter pipeline
Usage:
    python plot.py input.svg output.gcode [a4_landscape|a4_portrait]
               [--fill 0.6] [--hatch 0.6] [--hatch-angle 45]
               [--zigzag 0.6] [--zigzag-angle 45]
               [--mirror-y] [--preview] [--estimate]
"""
import sys
import re
import math
import io
import numpy as np
import vpype as vp
import svgelements
import xml.etree.ElementTree as ET
from shapely.geometry import Polygon, LineString
from shapely.affinity import rotate
from shapely.ops import unary_union
import shapely

# ── Machine config ───────────────────────────────────────────────────────────
QUANTIZATION  = 1.0
MERGE_TOL     = 0.5
PEN_UP_Z      = 0
PEN_DOWN_Z    = -15
FEED_Z        = 1000
FEED_TRAVEL   = 3500
FEED_DRAW     = 3000
MM_PER_PX     = 1 / 3.7795275591
PX_PER_MM     = 3.7795275591
PEN_LIFT_SEC  = 3       # seconds per pen up+down cycle

# ── Fill config ──────────────────────────────────────────────────────────────
# FIX (Bug 1): Removed overly restrictive area gates that were rejecting
# real shapes. 1 px² min avoids degenerate points; no practical upper bound.
MIN_FILL_AREA = 1
MAX_FILL_AREA = 1e9

# ── Page presets (mm) ────────────────────────────────────────────────────────
PAGES = {
    "a4_landscape": (250, 190),
    "a4_portrait":  (190, 250),
}

# ── CLI ──────────────────────────────────────────────────────────────────────
def parse_args():
    argv = sys.argv[1:]
    if len(argv) < 2:
        print(__doc__)
        sys.exit(1)
    args = {
        "infile":   argv[0],
        "outfile":  argv[1],
        "page":     None,
        "fill":     None,
        "hatch":    None,
        "zigzag":   None,
        "mirror_y": False,
        "preview":  False,
        "estimate": False,
    }
    hatch_spacing  = None
    hatch_angle    = 45.0
    zigzag_spacing = None
    zigzag_angle   = 45.0
    i = 2
    while i < len(argv):
        tok = argv[i]
        if tok in PAGES:
            args["page"] = tok
        elif tok == "--fill":
            i += 1; args["fill"] = float(argv[i])
        elif tok == "--hatch":
            i += 1; hatch_spacing = float(argv[i])
        elif tok == "--hatch-angle":
            i += 1; hatch_angle = float(argv[i])
        elif tok == "--zigzag":
            i += 1; zigzag_spacing = float(argv[i])
        elif tok == "--zigzag-angle":
            i += 1; zigzag_angle = float(argv[i])
        elif tok == "--mirror-y":
            args["mirror_y"] = True
        elif tok == "--preview":
            args["preview"] = True
        elif tok == "--estimate":
            args["estimate"] = True
        i += 1
    if hatch_spacing  is not None: args["hatch"]  = (hatch_spacing,  hatch_angle)
    if zigzag_spacing is not None: args["zigzag"] = (zigzag_spacing, zigzag_angle)
    return args

# ── SVG fill extraction ──────────────────────────────────────────────────────
def parse_svg_styles(svg_path):
    """
    Extract fill values from <style> blocks.
    Returns dict: selector_name → fill_color_string
    Handles both .classname and #id selectors.
    """
    tree = ET.parse(svg_path)
    root = tree.getroot()
    ns = {'svg': 'http://www.w3.org/2000/svg'}
    style_map = {}
    for style_elem in root.findall('.//svg:style', ns):
        css = style_elem.text or ''
        for block in re.finditer(r'([.#][\w-]+)\s*\{([^}]+)\}', css):
            selector = block.group(1).lstrip('.#')
            fill_m   = re.search(r'fill\s*:\s*([^;}\s]+)', block.group(2))
            if fill_m:
                style_map[selector] = fill_m.group(1).strip().lower()
    if style_map:
        print(f"  CSS styles found: {list(style_map.keys())[:10]}")
    return style_map


def get_element_fill_from_xml(svg_path):
    """
    FIX (Bug 4): Secondary raw-XML pass to capture fill values that
    svgelements misses — particularly group-level presentation attributes
    and fills inherited through the element tree.

    Returns dict: element id → fill colour string
    """
    tree = ET.parse(svg_path)
    root = tree.getroot()
    ns_strip = re.compile(r'\{[^}]+\}')
    id_fill = {}

    def walk(elem, inherited='none'):
        tag = ns_strip.sub('', elem.tag)
        style = elem.get('style', '')
        m = re.search(r'fill\s*:\s*([^;}\s]+)', style)
        if m:
            fill = m.group(1).lower()
        else:
            fill = elem.get('fill', inherited).lower()
        eid = elem.get('id', '')
        if eid and fill not in ('none', ''):
            id_fill[eid] = fill
        for child in elem:
            walk(child, fill)

    walk(root)
    return id_fill


def resolve_fill(elem, style_map, xml_id_fill):
    """
    Resolve the effective fill of a svgelements Path, checking (in order):
      1. inline style= attribute
      2. fill= attribute (svgelements parsed)
      3. class → style_map
      4. id → style_map
      5. id → xml_id_fill (raw XML fallback — FIX Bug 4)
      6. parent chain inheritance
    Returns a colour string, or None if no fill / fill is none.
    """
    NONE_VALS = {'none', '', 'transparent'}

    def _clean(val):
        v = str(val).strip().lower()
        return None if v in NONE_VALS else v

    # 1. inline style attribute
    inline = (elem.values or {}).get('style', '')
    if inline:
        m = re.search(r'fill\s*:\s*([^;}\s]+)', inline)
        if m:
            c = _clean(m.group(1))
            if c: return c

    # 2. svgelements parsed fill attr
    c = _clean(elem.fill)
    if c: return c

    # 3. class lookup
    raw_class = (elem.values or {}).get('class', '')
    for cls in raw_class.split():
        if cls in style_map:
            c = _clean(style_map[cls])
            if c: return c

    # 4. id → CSS style_map
    elem_id = (elem.values or {}).get('id', '')
    if elem_id in style_map:
        c = _clean(style_map[elem_id])
        if c: return c

    # 5. FIX (Bug 4): id → raw XML fill map
    if elem_id in xml_id_fill:
        c = _clean(xml_id_fill[elem_id])
        if c: return c

    # 6. walk up parent chain
    parent = getattr(elem, 'parent', None)
    while parent is not None:
        c = _clean(getattr(parent, 'fill', None))
        if c: return c
        raw_class = (getattr(parent, 'values', None) or {}).get('class', '')
        for cls in raw_class.split():
            if cls in style_map:
                c = _clean(style_map[cls])
                if c: return c
        parent_id = (getattr(parent, 'values', None) or {}).get('id', '')
        if parent_id in xml_id_fill:
            c = _clean(xml_id_fill[parent_id])
            if c: return c
        parent = getattr(parent, 'parent', None)

    return None


def sample_segment(seg, steps=12):
    """Sample a svgelements path segment into (x, y) point list."""
    return [(seg.point(i / steps).x, seg.point(i / steps).y)
            for i in range(steps + 1)]


def subpaths_to_shape(subpaths, fill_rule='nonzero'):
    """
    FIX (Bug 2): Build a Shapely geometry from sub-path coordinate lists,
    respecting SVG fill-rule (nonzero or evenodd).

    Even-odd:  each successive sub-path toggles filled/unfilled via
               symmetric_difference — correctly punches holes in donuts,
               letters with counters, etc.
    Non-zero:  contained sub-paths become holes (difference); external
               sub-paths are additive (union). Matches SVG spec winding
               behaviour for the common case.
    """
    polys = []
    for coords in subpaths:
        if len(coords) < 3:
            continue
        try:
            p = Polygon(coords)
            if not p.is_valid:
                p = p.buffer(0)
            if not p.is_empty:
                polys.append(p)
        except Exception:
            continue

    if not polys:
        return None

    if fill_rule == 'evenodd':
        result = polys[0]
        for p in polys[1:]:
            result = result.symmetric_difference(p)
    else:
        # Sort largest-first so outer shell comes first
        polys.sort(key=lambda p: p.area, reverse=True)
        result = polys[0]
        for p in polys[1:]:
            if result.contains(p):
                result = result.difference(p)   # punch hole
            else:
                result = result.union(p)         # additive

    return result if not result.is_empty else None


def get_filled_shapes(svg_path):
    """
    Parse SVG and return Shapely Polygons for every filled path element.
    Handles inline fill attrs, CSS <style> blocks, class/id selectors,
    parent-group fill inheritance, even-odd fill rule, and raw XML fallback.
    """
    style_map   = parse_svg_styles(svg_path)
    xml_id_fill = get_element_fill_from_xml(svg_path)   # FIX Bug 4
    svg         = svgelements.SVG.parse(svg_path)
    shapes      = []

    for elem in svg.elements():
        if not isinstance(elem, svgelements.Path):
            continue

        fill = resolve_fill(elem, style_map, xml_id_fill)
        if fill is None:
            continue

        # FIX (Bug 2): detect fill-rule from inline style or attribute
        fill_rule = 'nonzero'
        inline_style = (elem.values or {}).get('style', '')
        fr_m = re.search(r'fill-rule\s*:\s*(\w+)', inline_style)
        if fr_m:
            fill_rule = fr_m.group(1).lower()
        elif (elem.values or {}).get('fill-rule'):
            fill_rule = (elem.values or {}).get('fill-rule').lower()

        # Walk segments, splitting on Move and Close
        subpaths = []
        current  = []
        for seg in elem:
            if isinstance(seg, svgelements.Move):
                if current:
                    subpaths.append(current)
                current = [(seg.end.x, seg.end.y)]
            elif isinstance(seg, svgelements.Close):
                if current:
                    subpaths.append(current)
                current = []
            else:
                current.extend(sample_segment(seg, steps=12))
        if current:
            subpaths.append(current)

        # FIX (Bug 2): use fill-rule-aware shape builder
        shape = subpaths_to_shape(subpaths, fill_rule)
        if shape is None:
            continue

        # FIX (Bug 1): MIN_FILL_AREA is now 1 (was 50), MAX is 1e9 (was 100k)
        def _add(s):
            if s.area >= MIN_FILL_AREA:
                shapes.append(s)

        if shape.geom_type == 'Polygon':
            _add(shape)
        elif shape.geom_type == 'MultiPolygon':
            for g in shape.geoms:
                _add(g)

    print(f"  Filled shapes found: {len(shapes)}")

    # Debug: show bounds of first shape to catch transform issues
    if shapes:
        b = shapes[0].bounds
        print(f"  First shape bounds (px): {[round(v,1) for v in b]}")
        print(f"  First shape bounds (mm): {[round(v*MM_PER_PX,1) for v in b]}")

    return shapes

# ── Fill / hatch generators ──────────────────────────────────────────────────
def _shapes_to_lc(shapes):
    """Convert a list of Shapely Polygons to a vpype LineCollection."""
    out = vp.LineCollection()
    for shape in shapes:
        boundary = shape.boundary
        for g in getattr(boundary, 'geoms', [boundary]):
            coords = list(g.coords)
            if len(coords) >= 2:
                out.append(np.array([complex(x, y) for x, y in coords]))
    return out


def offset_fill(shapes, spacing_mm):
    """
    Inward offset fill (concentric rings).
    FIX (Bug 5): Start at offset=0 (the shape boundary itself) so the
    outermost ring is always included, then step inward by spacing_px.
    """
    spacing_px = spacing_mm * PX_PER_MM
    out = vp.LineCollection()
    for shape in shapes:
        offset = 0                          # FIX: was -spacing_px (skipped outer ring)
        while True:
            shrunk = shape.buffer(offset)
            if shrunk.is_empty:
                break
            boundary = shrunk.boundary
            for g in getattr(boundary, 'geoms', [boundary]):
                coords = list(g.coords)
                if len(coords) >= 2:
                    out.append(np.array([complex(x, y) for x, y in coords]))
            offset -= spacing_px
    print(f"  Generated {len(out)} offset fill lines")
    return out


def parallel_hatch(shapes, spacing_mm, angle_deg=45.0):
    """
    Boustrophedon (alternating-direction) parallel hatch.
    FIX (Bug 3): Capture shape.centroid BEFORE rotating so the rotate-back
    uses the same origin — was using the rotated shape's centroid which
    caused hatch lines to drift off the original shape.
    """
    spacing_px = spacing_mm * PX_PER_MM
    out = vp.LineCollection()
    for shape in shapes:
        origin = shape.centroid             # FIX: capture before any rotation
        rot    = rotate(shape, -angle_deg, origin=origin)
        minx, miny, maxx, maxy = rot.bounds
        y    = miny
        flip = False
        while y <= maxy:
            scan    = LineString([(minx - 1, y), (maxx + 1, y)])
            clipped = rot.intersection(scan)
            if not clipped.is_empty:
                geoms = sorted(
                    getattr(clipped, 'geoms', [clipped]),
                    key=lambda g: g.coords[0][0],
                    reverse=flip,
                )
                for g in geoms:
                    coords = list(g.coords)
                    if flip:
                        coords = coords[::-1]
                    if len(coords) >= 2:
                        rl = rotate(LineString(coords), angle_deg, origin=origin)  # FIX: same origin
                        fc = list(rl.coords)
                        out.append(np.array([complex(x, y) for x, y in fc]))
                flip = not flip
            y += spacing_px
    print(f"  Generated {len(out)} hatch lines")
    return out

def zigzag_fill(shapes, spacing_mm, angle_deg=45.0):
    spacing_px = spacing_mm * PX_PER_MM
    expected_bridge = spacing_px / math.sin(math.radians(angle_deg))

    # Cluster-merge shapes within spacing_mm of each other
    remaining = list(shapes)
    merged = []
    while remaining:
        cluster = remaining.pop(0)
        changed = True
        while changed:
            changed = False
            next_remaining = []
            for s in remaining:
                if cluster.distance(s) < spacing_px:
                    cluster = cluster.union(s)
                    changed = True
                else:
                    next_remaining.append(s)
            remaining = next_remaining
        merged.append(cluster)
    print(f"  Merged {len(shapes)} shapes → {len(merged)} regions")

    # Explode MultiPolygons
    exploded = []
    for s in merged:
        if s.geom_type == 'MultiPolygon':
            exploded.extend(s.geoms)
        else:
            exploded.append(s)
    print(f"  Exploded to {len(exploded)} individual polygons")
    shapes = exploded

    out = vp.LineCollection()
    for shape in shapes:
        origin = shape.centroid
        rot    = rotate(shape, -angle_deg, origin=origin)
        minx, miny, maxx, maxy = rot.bounds

        # Collect all scan segments across the whole bounding box
        all_segs = []
        y    = miny
        flip = False
        while y <= maxy:
            scan    = LineString([(minx - 1, y), (maxx + 1, y)])
            clipped = rot.intersection(scan)
            if not clipped.is_empty:
                geoms = sorted(
                    getattr(clipped, 'geoms', [clipped]),
                    key=lambda g: g.coords[0][0],
                    reverse=flip,
                )
                for g in geoms:
                    coords = list(g.coords)
                    if flip:
                        coords = coords[::-1]
                    if len(coords) >= 2:
                        all_segs.append(coords)
            flip = not flip
            y += spacing_px

        # Stitch all segments into continuous paths, only lifting when
        # the gap is larger than an expected inter-row bridge
        full_path = []
        for seg in all_segs:
            if not full_path:
                full_path.extend(seg)
            else:
                end   = full_path[-1]
                start = seg[0]
                dist  = math.hypot(end[0] - start[0], end[1] - start[1])
                if dist < expected_bridge * 1.2:
                    full_path.append(start)
                    full_path.extend(seg)
                else:
                    rl = rotate(LineString(full_path), angle_deg, origin=origin)
                    out.append(np.array([complex(x, y) for x, y in rl.coords]))
                    full_path = list(seg)

        if full_path:
            rl = rotate(LineString(full_path), angle_deg, origin=origin)
            out.append(np.array([complex(x, y) for x, y in rl.coords]))

    print(f"  Generated {len(out)} zigzag paths")
    return out

# ── Toolpath optimisation ────────────────────────────────────────────────────
def linesort(lc):
    """Nearest-neighbour pen-up travel optimisation."""
    if len(lc) <= 1:
        return lc
    line_index = vp.LineIndex(lc[1:], reverse=True)
    new_lc = lc.clone([lc[0]])
    while len(line_index) > 0:
        idx, reverse = line_index.find_nearest(new_lc[-1][-1])
        line = line_index.pop(idx)
        if line is not None:
            if reverse:
                line = np.flip(line)
            new_lc.append(line)
    return new_lc

# ── Layout helpers ───────────────────────────────────────────────────────────
def fit_to_page(lc, page_key):
    page_w, page_h = PAGES[page_key]
    bounds = lc.bounds()
    if not bounds:
        return lc

    src_w = bounds[2] - bounds[0]
    src_h = bounds[3] - bounds[1]

    scale = min(page_w / src_w, page_h / src_h)

    lc.translate(-bounds[0], -bounds[1])
    lc.scale(scale)

    print(f"  Scaled to fit {page_key} (×{scale:.3f})")
    return lc


def mirror_y(lc):
    bounds = lc.bounds()
    if not bounds:
        return lc
    cy = (bounds[1] + bounds[3]) / 2
    lc.translate(0, -cy)
    lc.scale(1, -1)
    lc.translate(0, cy)
    print("  Mirrored Y")
    return lc


def origin_to_zero(lc):
    bounds = lc.bounds()
    if bounds:
        lc.translate(-bounds[0], -bounds[1])
    return lc

# ── Estimation & preview ─────────────────────────────────────────────────────
def estimate_time(lc):
    total_draw_mm   = 0.0
    total_travel_mm = 0.0
    last_pt         = None
    for line in lc:
        pts = [(pt.real * MM_PER_PX, pt.imag * MM_PER_PX) for pt in line]
        if not pts:
            continue
        if last_pt is not None:
            dx = pts[0][0] - last_pt[0]
            dy = pts[0][1] - last_pt[1]
            total_travel_mm += math.hypot(dx, dy)
        for i in range(1, len(pts)):
            total_draw_mm += math.hypot(pts[i][0] - pts[i-1][0],
                                        pts[i][1] - pts[i-1][1])
        last_pt = pts[-1]
    num_lifts  = len(lc)
    draw_sec   = (total_draw_mm   / FEED_DRAW)   * 60
    travel_sec = (total_travel_mm / FEED_TRAVEL) * 60
    lift_sec   = num_lifts * PEN_LIFT_SEC
    total_sec  = draw_sec + travel_sec + lift_sec
    total_min  = total_sec / 60
    print(f"\n  ── Estimate ──────────────────────────────────")
    print(f"  Draw distance:   {total_draw_mm/1000:.2f} m")
    print(f"  Travel distance: {total_travel_mm/1000:.2f} m")
    print(f"  Pen lifts:       {num_lifts}")
    print(f"  Draw time:       {draw_sec/60:.1f} min")
    print(f"  Travel time:     {travel_sec/60:.1f} min")
    print(f"  Lift time:       {lift_sec/60:.1f} min  ({num_lifts} × {PEN_LIFT_SEC}s)")
    if total_min >= 60:
        print(f"  Total:           {int(total_min//60)}h {int(total_min%60)}min")
    else:
        print(f"  Total:           {int(total_min)} min")
    print(f"  ──────────────────────────────────────────────\n")


def preview(lc):
    try:
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        print("  pip install matplotlib  to enable preview")
        return
    fig, ax = plt.subplots(figsize=(12, 10))
    ax.set_facecolor('#f5f5f0')
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_aspect('equal')
    last_pt = None
    for line in lc:
        pts = [(pt.real, pt.imag) for pt in line]
        if not pts:
            continue
        if last_pt is not None:
            ax.plot([last_pt[0], pts[0][0]], [last_pt[1], pts[0][1]],
                    color='#ff4444', alpha=0.4, linewidth=0.5, linestyle='--')
        ax.plot([p[0] for p in pts], [p[1] for p in pts],
                color='#1a1aff', alpha=0.8, linewidth=0.6)
        last_pt = pts[-1]
    ax.legend(handles=[
        mpatches.Patch(color='#1a1aff', label='Drawing'),
        mpatches.Patch(color='#ff4444', label='Pen-up travel'),
    ], facecolor='#2a2a4e', labelcolor='white')
    ax.set_title('Toolpath Preview', color='white', pad=12)
    ax.tick_params(colors='#888888')
    for spine in ax.spines.values():
        spine.set_edgecolor('#444444')
    plt.tight_layout()
    plt.show()

# ── G-code output ────────────────────────────────────────────────────────────
def write_gcode(lc, outfile):
    with open(outfile, 'w') as f:
        f.write("G21\nG90\nG92 X0 Y0 Z0\n")
        for line in lc:
            f.write(f"G1 Z{PEN_UP_Z} F{FEED_Z}\n")
            first = True
            for pt in line:
                x = round(pt.real, 3)
                y = round(pt.imag, 3)
                if first:
                    f.write(f"G0 X{x} Y{y} F{FEED_TRAVEL}\n")
                    f.write(f"G1 Z{PEN_DOWN_Z} F{FEED_Z}\n")
                    first = False
                else:
                    f.write(f"G1 X{x} Y{y} F{FEED_DRAW}\n")
        f.write(f"G1 Z{PEN_UP_Z} F{FEED_Z}\n")
        f.write("G0 X0 Y0\n")

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    args = parse_args()

    # ── Load SVG strokes ──────────────────────────────
    print("Reading SVG...")
    lc, w, h = vp.read_svg(args["infile"], quantization=QUANTIZATION)
    lc.scale(MM_PER_PX)
    print(f"  Loaded {len(lc)} lines")
    lc.merge(tolerance=MERGE_TOL, flip=True)
    print(f"  After merge: {len(lc)} lines")

    # ── Fill operations (shared shape extraction) ─────
    fill_needed = args["fill"] or args["hatch"] or args["zigzag"]
    shapes = get_filled_shapes(args["infile"]) if fill_needed else []

    # Get vpype bounds
    lc_bounds = lc.bounds()
    sx = (lc_bounds[2] - lc_bounds[0])
    sy = (lc_bounds[3] - lc_bounds[1])

    # Get shape bounds
    from shapely.ops import unary_union
    shape_union = unary_union(shapes)
    sb = shape_union.bounds
    sw = sb[2] - sb[0]
    sh = sb[3] - sb[1]

    # Compute scale + offset
    scale = sx / sw if sw != 0 else 1

    aligned_shapes = []
    for s in shapes:
        s2 = shapely.affinity.scale(s, xfact=scale, yfact=scale, origin=(0,0))
        s2 = shapely.affinity.translate(s2,
            xoff=lc_bounds[0] - sb[0]*scale,
            yoff=lc_bounds[1] - sb[1]*scale
        )
        aligned_shapes.append(s2)

    shapes = aligned_shapes

    if args["fill"] is not None:
        print(f"Generating offset fill ({args['fill']} mm)...")
        lc.extend(offset_fill(shapes, args["fill"]))

    if args["hatch"] is not None:
        spacing, angle = args["hatch"]
        print(f"Generating parallel hatch ({spacing} mm, {angle}°)...")
        lc.extend(parallel_hatch(shapes, spacing, angle))

    if args["zigzag"] is not None:
        spacing, angle = args["zigzag"]
        print(f"Generating zigzag fill ({spacing} mm, {angle}°)...")
        lc.extend(zigzag_fill(shapes, spacing, angle))

    # ── Layout ────────────────────────────────────────
    if args["mirror_y"]:
        lc = mirror_y(lc)
    if args["page"]:
        if args["page"] not in PAGES:
            print(f"Unknown page preset: {args['page']}")
            sys.exit(1)
        lc = fit_to_page(lc, args["page"])
    lc = origin_to_zero(lc)

    # ── Optimise ──────────────────────────────────────
    lc = linesort(lc)
    print(f"  After sort: {len(lc)} lines")

    # ── Output ────────────────────────────────────────
    if args["estimate"]:
        estimate_time(lc)
    if args["preview"]:
        preview(lc)
    write_gcode(lc, args["outfile"])
    print(f"Written to {args['outfile']}")

if __name__ == "__main__":
    main()