import vpype as vp
import numpy as np

MERGE_TOL = 0.5
lc, w, h = vp.read_svg("../../../Downloads/floral.svg", quantization=1.0)
print(f"Total lines: {len(lc)}")

closed = 0
for line in lc:
    if len(line) < 3:
        continue
    if np.isclose(line[0], line[-1], atol=MERGE_TOL):
        closed += 1

print(f"Closed paths: {closed}")
print(f"Sample line[0] vs line[-1]: {lc[0][0]} vs {lc[0][-1]}")
print(f"Difference: {abs(lc[0][0] - lc[0][-1])}")
