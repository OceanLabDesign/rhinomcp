---
name: rhino-exec
description: >-
  Run RhinoCommon C# through rhinomcp with an execute-verify loop, plus reliable
  geometry-surgery recipes (hole-cutting, loft + seam alignment, G1 continuity, join)
  and known pitfalls. Use when scripting geometry via execute_rhinocommon_csharp_code,
  building analysis tools, or debugging Rhino MCP calls.
---

# rhino-exec — RhinoCommon C# execute-verify loop & geometry recipes

> [!NOTE]
> Skeleton skill (Phase 1). The recipes below are battle-tested; expand the TODOs as
> the analysis-evaluation layer grows.

## Execute-verify loop

- Write C# → call `execute_rhinocommon_csharp_code` → read `output` and fix on the
  reported error. **First versions are expected to error** — that's the verification
  loop, not failure. Globals available: `doc` (active `RhinoDoc`), `output` (a
  `StringBuilder`; use `output.AppendLine(...)`). Common namespaces are pre-imported.
- **Heavy scripts (>~15 s)** return `Communication error with Rhino: No data received`,
  **but Rhino usually finished and added the geometry**. Probe with
  `get_document_summary` (object count / bbox / per-layer counts) before re-running —
  re-running duplicates geometry. Batch heavy work (≤ ~12 items) to stay under the timeout.

## Geometry-surgery recipes

- **Pick the base brep, excluding output layers.** When enumerating with
  `ObjectEnumeratorSettings { HiddenObjects = true }`, filtering only by `area > X`
  can grab a leftover half-product on your output layer and cut *that*. Also check
  `o.Attributes.LayerIndex != outputLayer`.
- **Cut a circular hole reliably.** `Curve.ProjectToBrep` of a circle often fragments
  (multiple segments / `IsClosed == false`); feeding that straight to `Split` shatters
  the face. Instead: per hole, `ProjectToBrep` + `Split` once, take the small piece's
  naked edge as the *exact* hole edge, collect them, then one-shot `Split`.
- **G1 join — do NOT use `CreateBlendSurface` on closed edges.** It twists, even when
  the two edges' seam directions are aligned to `angle diff = 0°`. Use a **loft** whose
  section circles all share one seam direction `v0` —
  `new Plane(center, v0, Vector3d.CrossProduct(N, v0))` — so it cannot twist; put **two
  `z = 0` rings at the root** to force a horizontal, G1 start into the base surface.
  `LoftType.Normal`. (Loft is also faster than blend.)
- **Join tolerance.** Generated edges vs. trimmed hole edges have micron gaps;
  `JoinBreps` needs a widened tol (~5–20× `ModelAbsoluteTolerance`) to close them into
  a single brep. Sanity check a finished spike: `naked edges = 4 outer + 1 tip per spike`.

## Hard rule: never guess RhinoCommon APIs

Verify usage against the official RhinoCommon docs, or mark `TODO` with how you'll
check it at runtime. IronPython 2.7 (legacy `execute_rhinoscript_python_code`) returns
`out` params as tuples whose order must be confirmed against the docs.

<!-- TODO: link a reference.md with the validated-API table; add companion skills
     new-analysis-tool (scaffold the 5-part tool) and build-install (yak + venv). -->
