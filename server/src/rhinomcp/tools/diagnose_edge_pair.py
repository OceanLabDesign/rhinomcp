from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from rhinomcp.server import get_rhino_connection, mcp
from typing import Any, Dict


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def diagnose_edge_pair(ctx: Context, exclude_extrusion: bool = False) -> Dict[str, Any]:
    """
    Diagnose the relationship between the two currently selected Brep edges.

    Before calling: in Rhino, sub-object select exactly two surface edges
    (Ctrl+Shift click the two edges), then call this tool. It reads only the
    current selection and never modifies the document.

    Parameters:
    - exclude_extrusion: When False (default), an edge picked on an Extrusion is
      still measured but flagged unverified, with its endpoints exposed in
      measurements.extrusion_edges so you can confirm the pick in Rhino. Set True
      to skip Extrusion edges entirely when their ToBrep() index mapping is known
      to be unreliable.

    Returns:
    - diagnosis: gap | misalignment | unjoined_coincident | not_a_gap
    - edges: per-edge id, parent_brep_id, is_naked, valence, length, degree
    - gap: max / mean / min distance between the two edges, and whether uniform
    - continuity_current: G-1 | G0 | G1 | G2
    - boundary_complexity: two_edge | multi_edge
    - near_planar: whether the two edges are nearly coplanar
    - recommended_roadmaps: ranked repair playbooks
      (strategy / continuity / confidence / steps / why)
    - warnings: e.g. non-manifold risk when an edge is not naked
    - measurements: the raw numbers behind the verdict

    Diagnose only. This tool never repairs or modifies geometry.
    """
    rhino = get_rhino_connection()
    return rhino.send_command("diagnose_edge_pair", {"exclude_extrusion": exclude_extrusion})
