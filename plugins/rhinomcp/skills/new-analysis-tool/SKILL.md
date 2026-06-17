---
name: new-analysis-tool
description: >-
  Scaffold a new read-only "analysis-evaluation" MCP tool for the rhinomcp (OceanLab fork),
  following the analyze_objects / diagnose_edge_pair convention — the 5 parts: protocol enum +
  command/response JSON contracts + Python @mcp.tool wrapper + C# handler + read-only retry
  whitelist. Use when adding a new diagnose_* / analyze_* geometry analysis tool to this repo.
argument-hint: <command_name>
---

# new-analysis-tool — scaffold a read-only analysis-evaluation tool

Build the 5-part skeleton of a new perception/analysis tool, copying the existing
`analyze_objects` (simplest read-only tool) and `diagnose_edge_pair` (full analysis-evaluation)
conventions. **This scaffolds; the real measurement logic you write into the C# handler.**

## 0. Settle these first

Ask / confirm with the user:
- **`<cmd>`** — snake_case command name (e.g. `diagnose_edge_pair`). PascalCase form `<Cmd>` for
  the C# method, `<Feature>.cs` for the file.
- One-line purpose, input params, and the output JSON fields.

**Iron rules (from CLAUDE.md — do not break):**
- **Perceive only, never act**: measure / classify / rank advice / warn. No repair, no BlendSrf /
  join / bake / document writes. In-memory copies (`DuplicateCurve` etc.) are fine; writing back
  is not.
- **Measurement logic lives in the C# handler** (not IronPython). The C# handler is the single
  source of truth.
- **Tolerance from `doc.ModelAbsoluteTolerance`** — never hard-code a number.
- **Read only the current selection** (`GetSelectedObjects` / `ObjectEnumeratorSettings`), never
  scan the whole scene (client 15 s timeout + UI-thread sync).
- **Never guess RhinoCommon APIs** — verify against official docs or mark `TODO` with how to
  check. First run is expected to error in Rhino; that's the execute-verify loop (see
  `rhino-exec`).

## The 5 parts (build in this order)

### 1 · protocol enum — `contracts/protocol.json`
Add one line to `$defs.command.properties.type.enum` (near `analyze_objects` / `diagnose_edge_pair`):
```json
            "<cmd>",
```

### 2 · command contract — `contracts/commands/<cmd>.json`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "<cmd>.json",
  "title": "<Cmd> Command",
  "description": "<what it does>. Reads the current selection only. Diagnose-only.",
  "type": "object",
  "properties": {
    "<param>": { "type": "boolean", "default": false, "description": "<...>" }
  },
  "additionalProperties": false
}
```

### 3 · response contract — `contracts/responses/<cmd>_result.json`
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "<cmd>_result.json",
  "title": "<Cmd> Response",
  "description": "<one line>. Diagnose-only; never modifies the document.",
  "type": "object",
  "properties": {
    "<field>": { "type": "string" }
  },
  "required": ["<field>"],
  "additionalProperties": false
}
```

### 4 · Python wrapper — `server/src/rhinomcp/tools/<cmd>.py`
Thin wrapper only — no logic here, just forward to Rhino. Dropping the file in `tools/`
auto-registers it (no `__init__.py` edit needed).
```python
from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from rhinomcp.server import get_rhino_connection, mcp
from typing import Any, Dict


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))   # read-only annotation
def <cmd>(ctx: Context, <param>: bool = False) -> Dict[str, Any]:
    """
    <one-line summary>.

    Before calling: <selection precondition>. Reads only the current selection
    and never modifies the document.

    Returns:
    - <field>: <...>

    Diagnose only. This tool never repairs or modifies geometry.
    """
    rhino = get_rhino_connection()
    return rhino.send_command("<cmd>", {"<param>": <param>})
```

### 5 · C# handler — `plugin/Functions/<Feature>.cs`  ← the only implementation
```csharp
using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;

namespace RhinoMCPPlugin.Functions;

public partial class RhinoMCPFunctions
{
    [McpCommand("<cmd>", ReadOnly = true)]          // ReadOnly mirrors the Python readOnlyHint
    public JObject <Cmd>(JObject parameters)
    {
        var doc = RhinoDoc.ActiveDoc;
        double tol = doc.ModelAbsoluteTolerance;     // tolerance from the doc, never hard-coded

        bool <param> = parameters["<param>"]?.ToObject<bool>() ?? false;

        // 1) read ONLY the current selection (sub-object aware: ObjectEnumeratorSettings
        //    { SelectedObjectsFilter = true, SubObjectSelected = true } — see DiagnoseEdgePair.cs)
        // 2) measure on in-memory copies (DuplicateCurve, …) — never write back
        // 3) classify + rank roadmaps + collect warnings
        // 4) return a JObject whose shape matches contracts/responses/<cmd>_result.json

        return new JObject
        {
            // ... fields matching the response contract ...
        };
    }
}
```
Reuse existing numeric helpers (e.g. `DR(double)` for rounding) rather than re-inventing them.

### + retry whitelist — `server/src/rhinomcp/server.py`
Add `<cmd>` to the `READONLY_RETRY_COMMANDS` set (≈ line 70) so a transient Rhino socket drop is
retried once for this read-only tool.

### + tests
Add coverage following the existing tests (JSON-schema validation via
`contracts/test_schemas.py`, plus a wrapper test against the mock server).

## After scaffolding
1. `dotnet build plugin/rhinomcp.sln --configuration Release`
2. Reinstall the `.rhp` (yak — see [[rhino8-mac-plugin-install]]), restart Rhino, `mcpstart`.
3. Test. Expect the first version to throw inside Rhino — read the error, fix, repeat. That loop,
   plus reliable geometry recipes, is in `rhino-exec`.

## Copy-from references
- **`analyze_objects`** — simplest read-only tool (`server/.../analyze_objects.py` +
  `plugin/Functions/AnalyzeObjects.cs`).
- **`diagnose_edge_pair`** — full analysis-evaluation with all 5 parts; mirror its structure.
