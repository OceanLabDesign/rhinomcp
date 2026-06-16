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
    // 沿邊取樣點數(含端點)。純量測;公差一律取自文件,不寫死。
    private const int DiagSamples = 21;
    private const int DiagNormalSamples = 9;

    private class DiagEdge
    {
        public RhinoObject Obj;
        public Brep Brep;
        public string BrepId;
        public int Index;
        public BrepEdge Edge;
        public Curve Curve;        // in-memory 複本,不改文件
        public bool FromExtrusion;
        public string Valence;
        public bool IsNaked;
    }

    private class DiagGap
    {
        public double Max, Mean, Min, Span;
        public bool Uniform;
        public int N;
        public bool DevOk;
    }

    private class DiagEnds
    {
        public double EndGapAvg;
        public double[] EndGaps;
        public bool Reversed;
    }

    [McpCommand("diagnose_edge_pair", ReadOnly = true)]
    public JObject DiagnoseEdgePair(JObject parameters)
    {
        var doc = RhinoDoc.ActiveDoc;
        double tol = doc.ModelAbsoluteTolerance;   // 鐵則:公差取自文件

        // 預設走「驗證」(收 Extrusion 邊、附端點供比對);exclude_extrusion=true 改走
        // 「排除」(Extrusion 邊一律跳過,不量可能對不上的索引)。兩條路都在,呼叫端選。
        bool excludeExtrusion = parameters["exclude_extrusion"]?.ToObject<bool>() ?? false;

        var notes = new List<string>();
        var edges = DiagCollectSelectedEdges(doc, notes, excludeExtrusion);

        if (edges.Count != 2)
        {
            return new JObject
            {
                ["diagnosis"] = "not_a_gap",
                ["error"] = $"需要剛好兩條 brep 邊子物件,目前抓到 {edges.Count} 條。",
                ["how_to_select"] = "在 Rhino 用 Ctrl+Shift 點選兩條曲面邊緣(sub-object),再呼叫本工具。",
                ["edges"] = new JArray(),
                ["gap"] = JValue.CreateNull(),
                ["continuity_current"] = "G-1",
                ["boundary_complexity"] = "two_edge",
                ["near_planar"] = false,
                ["recommended_roadmaps"] = new JArray(),
                ["warnings"] = JArray.FromObject(notes),
                ["measurements"] = new JObject { ["tolerance"] = DR(tol) }
            };
        }

        var A = edges[0];
        var B = edges[1];

        var edgesJson = new JArray();
        foreach (var e in edges)
        {
            edgesJson.Add(new JObject
            {
                ["id"] = $"{e.BrepId}#edge{e.Index}",
                ["parent_brep_id"] = e.BrepId,
                ["index"] = e.Index,
                ["is_naked"] = e.IsNaked,
                ["valence"] = e.Valence,
                ["length"] = DR(e.Curve.GetLength()),
                ["degree"] = e.Curve.Degree
            });
        }

        bool bothNaked = A.IsNaked && B.IsNaked;

        var gap = DiagGapMetrics(A.Curve, B.Curve, tol);
        var ends = DiagEndpointPairing(A.Curve, B.Curve);
        double nearDev;
        bool nearPlanar = DiagNearPlanar(new[] { A.Curve, B.Curve }, tol, out nearDev);

        // 邊界複雜度:Phase 1 用端點配對啟發式(嚴謹做法走 naked edge loop 拓樸,待後續)。
        double refMax = gap != null ? gap.Max : tol;
        string boundary = ends.EndGapAvg <= Math.Max(2.0 * tol, 0.5 * refMax) ? "two_edge" : "multi_edge";

        string diagnosis, cont;
        DiagClassify(bothNaked, gap, ends, tol, out diagnosis, out cont);

        // 連續性升級:重合(G0)且法線夾角小 → 粗略 G1
        double? nang = null;
        if (gap != null && gap.Max <= tol && bothNaked)
        {
            nang = DiagNormalAngleDeg(A, B);
            if (nang.HasValue && nang.Value < 10.0) cont = "G1";
        }

        double la = A.Curve.GetLength();
        double lb = B.Curve.GetLength();
        double? lengthRatio = (la > 0 && lb > 0) ? (double?)(Math.Min(la, lb) / Math.Max(la, lb)) : null;

        var roadmaps = DiagRoadmaps(diagnosis, gap, nearPlanar, boundary, bothNaked);

        var warnings = new List<string>(notes);
        foreach (var e in edges)
            if (e.Valence != "naked")
                warnings.Add($"邊 {e.Index}(brep {e.BrepId})非 naked(valence={e.Valence}),在此 join/bridge 會產生 non-manifold。");
        if (gap != null && !gap.Uniform)
            warnings.Add($"間距不均勻(max={Rn(gap.Max)} min={Rn(gap.Min)} mean={Rn(gap.Mean)}):偏向錯位或非平行縫。");
        if (lengthRatio.HasValue && lengthRatio.Value < 0.5)
            warnings.Add($"兩邊長度比 {Rn(lengthRatio.Value, 4)} 偏小:放樣 / 橋接易扭轉,留意 seam 對齊。");
        if (A.BrepId == B.BrepId)
            warnings.Add("兩邊屬同一 brep(同物件上的兩條邊)。");
        if (gap != null && !gap.DevOk)
            warnings.Add("GetDistancesBetweenCurves 回 false,gap 數值改採沿邊取樣;請以此交叉確認。");

        // Extrusion 邊:不排除(它在 Rhino 太常見),改走「驗證」——照樣量,但把該邊
        // 端點/中點附進 measurements,讓使用者在 Rhino 比對實際點選的邊是否一致;
        // 對得上即可信任,對不上再退回幾何重映或排除。
        // TODO(驗證迴圈,鐵則 5):確認 ComponentIndex.Index 是否對應 ex.ToBrep().Edges[idx]。
        var extrusionEdgesJson = new JArray();
        foreach (var e in edges)
        {
            if (!e.FromExtrusion) continue;
            extrusionEdgesJson.Add(new JObject
            {
                ["index"] = e.Index,
                ["start"] = DiagPt(e.Curve.PointAtStart),
                ["end"] = DiagPt(e.Curve.PointAtEnd),
                ["mid"] = DiagPt(e.Curve.PointAt(e.Curve.Domain.Mid))
            });
            warnings.Add($"邊 {e.Index}(brep {e.BrepId})來自 Extrusion;ToBrep() 後邊索引對應尚未驗證(鐵則 5)。已把該邊端點/中點放進 measurements.extrusion_edges,請在 Rhino 比對你實際點選的邊——對得上即可信任,對不上回報我改幾何重映或排除。");
        }

        var endGapsJson = new JArray();
        foreach (var x in ends.EndGaps) endGapsJson.Add(DR(x));

        var measurements = new JObject
        {
            ["tolerance"] = DR(tol),
            ["length_ratio"] = lengthRatio.HasValue ? DR(lengthRatio.Value, 4) : JValue.CreateNull(),
            ["gap_span"] = gap != null ? DR(gap.Span) : JValue.CreateNull(),
            ["gap_samples"] = gap != null ? gap.N : 0,
            ["end_gap_avg"] = DR(ends.EndGapAvg),
            ["end_gaps"] = endGapsJson,
            ["normal_angle_deg"] = nang.HasValue ? DR(nang.Value, 3) : JValue.CreateNull(),
            ["near_planar_max_dev"] = nearDev >= 0.0 ? DR(nearDev) : JValue.CreateNull(),
            ["both_naked"] = bothNaked,
            ["extrusion_edges"] = extrusionEdgesJson
        };

        JToken gapJson = gap != null
            ? new JObject { ["max"] = DR(gap.Max), ["mean"] = DR(gap.Mean), ["min"] = DR(gap.Min), ["uniform"] = gap.Uniform }
            : JValue.CreateNull();

        return new JObject
        {
            ["diagnosis"] = diagnosis,
            ["edges"] = edgesJson,
            ["gap"] = gapJson,
            ["continuity_current"] = cont,
            ["boundary_complexity"] = boundary,
            ["near_planar"] = nearPlanar,
            ["recommended_roadmaps"] = roadmaps,
            ["warnings"] = JArray.FromObject(warnings),
            ["measurements"] = measurements
        };
    }

    // ---------------------------------------------------------------- helpers

    private static JToken DR(double v, int nd = 8) => Math.Round(v, nd);
    private static double Rn(double v, int nd = 8) => Math.Round(v, nd);
    private static JArray DiagPt(Point3d p) => new JArray { DR(p.X), DR(p.Y), DR(p.Z) };

    private List<DiagEdge> DiagCollectSelectedEdges(RhinoDoc doc, List<string> notes, bool excludeExtrusion)
    {
        var found = new List<DiagEdge>();
        int otherSelected = 0;

        // NOTE: GetSelectedObjects(false,false) does NOT return an object whose only
        // selection is a sub-object (edge) — which is exactly how edges get picked
        // (Ctrl+Shift click, or scripted SelectSubObject). Verified empirically. Use the
        // enumerator with SubObjectSelected=true so Rhino itself returns the objects
        // carrying a selected sub-object (targeted query, not a full-scene scan).
        var selSettings = new ObjectEnumeratorSettings
        {
            SelectedObjectsFilter = true,
            SubObjectSelected = true,
        };
        foreach (var obj in doc.Objects.GetObjectList(selSettings))
        {
            var cis = obj.GetSelectedSubObjects();   // ComponentIndex[] 或 null
            int pickedHere = 0;
            if (cis != null)
            {
                foreach (var ci in cis)
                {
                    if (ci.ComponentIndexType != ComponentIndexType.BrepEdge) continue;

                    bool fromExt;
                    var brep = DiagGetBrep(obj, out fromExt);
                    if (brep == null) { notes.Add($"物件 {obj.Id} 無法轉成 Brep,略過。"); continue; }

                    if (fromExt && excludeExtrusion)
                    {
                        notes.Add($"物件 {obj.Id} 為 Extrusion,已依 exclude_extrusion=true 排除該邊(ToBrep 邊索引對應未驗證)。");
                        continue;
                    }

                    int idx = ci.Index;
                    if (idx < 0 || idx >= brep.Edges.Count)
                    {
                        notes.Add($"物件 {obj.Id} edge index {idx} 超出範圍,略過。");
                        continue;
                    }

                    var edge = brep.Edges[idx];
                    string val = DiagValence(edge);
                    found.Add(new DiagEdge
                    {
                        Obj = obj,
                        Brep = brep,
                        BrepId = obj.Id.ToString(),
                        Index = idx,
                        Edge = edge,
                        Curve = edge.DuplicateCurve(),
                        FromExtrusion = fromExt,
                        Valence = val,
                        IsNaked = (val == "naked")
                    });
                    pickedHere++;
                    // Extrusion 的 verify 警告 + 端點探針改在主函式統一發(那裡有 Curve 幾何)。
                }
            }
            if (pickedHere == 0) otherSelected++;
        }

        if (otherSelected > 0)
            notes.Add($"另有 {otherSelected} 個選取物件非 brep 邊子物件(可能整體選取或獨立曲線);本工具只看 brep 邊子物件。");

        return found;
    }

    private Brep DiagGetBrep(RhinoObject obj, out bool fromExtrusion)
    {
        fromExtrusion = false;
        var geo = obj.Geometry;
        if (geo is Brep b) return b;
        if (geo is Extrusion ex) { fromExtrusion = true; return ex.ToBrep(); }
        return Brep.TryConvertBrep(geo);
    }

    private string DiagValence(BrepEdge edge)
    {
        switch (edge.Valence)
        {
            case EdgeAdjacency.Naked: return "naked";
            case EdgeAdjacency.Interior: return "interior";
            case EdgeAdjacency.NonManifold: return "nonmanifold";
            default: return "none";
        }
    }

    private List<double> DiagSampleParams(Curve crv, int n)
    {
        var ts = crv.DivideByCount(n - 1, true);   // double[] 或 null
        if (ts == null)
        {
            var dom = crv.Domain;
            var res = new List<double>();
            for (int i = 0; i < n; i++) res.Add(dom.ParameterAt((double)i / (n - 1)));
            return res;
        }
        return ts.ToList();
    }

    private DiagGap DiagGapMetrics(Curve cA, Curve cB, double tol)
    {
        var dists = new List<double>();
        var pairs = new[] { new[] { cA, cB }, new[] { cB, cA } };
        foreach (var pr in pairs)
        {
            var src = pr[0];
            var dst = pr[1];
            foreach (var t in DiagSampleParams(src, DiagSamples))
            {
                var p = src.PointAt(t);
                double tt;
                if (dst.ClosestPoint(p, out tt))
                    dists.Add(p.DistanceTo(dst.PointAt(tt)));
            }
        }

        double maxDist, maxA, maxB, minDist, minA, minB;
        bool devOk = Curve.GetDistancesBetweenCurves(cA, cB, tol,
            out maxDist, out maxA, out maxB, out minDist, out minA, out minB);

        if (dists.Count == 0 && !devOk) return null;

        double mx, mn, mean;
        if (dists.Count > 0) { mx = dists.Max(); mn = dists.Min(); mean = dists.Average(); }
        else { mx = mn = mean = maxDist; }
        if (devOk) { mx = Math.Max(mx, maxDist); mn = Math.Min(mn, minDist); }

        double span = mx - mn;
        bool uniform = mean > 0 ? span <= Math.Max(2.0 * tol, 0.10 * mean) : span <= 2.0 * tol;

        return new DiagGap { Max = mx, Mean = mean, Min = mn, Span = span, Uniform = uniform, N = dists.Count, DevOk = devOk };
    }

    private DiagEnds DiagEndpointPairing(Curve cA, Curve cB)
    {
        var a0 = cA.PointAtStart; var a1 = cA.PointAtEnd;
        var b0 = cB.PointAtStart; var b1 = cB.PointAtEnd;
        double straight = a0.DistanceTo(b0) + a1.DistanceTo(b1);
        double crossed = a0.DistanceTo(b1) + a1.DistanceTo(b0);
        double[] eg = straight <= crossed
            ? new[] { a0.DistanceTo(b0), a1.DistanceTo(b1) }
            : new[] { a0.DistanceTo(b1), a1.DistanceTo(b0) };
        return new DiagEnds { EndGapAvg = (eg[0] + eg[1]) / 2.0, EndGaps = eg, Reversed = crossed < straight };
    }

    private bool DiagNearPlanar(Curve[] curves, double tol, out double maxDev)
    {
        maxDev = -1.0;
        try
        {
            var pts = new List<Point3d>();
            double size = 0.0;
            foreach (var c in curves)
            {
                size = Math.Max(size, c.GetLength());
                foreach (var t in DiagSampleParams(c, DiagSamples))
                    pts.Add(c.PointAt(t));
            }
            Plane plane;
            var res = Plane.FitPlaneToPoints(pts, out plane);
            if (res != PlaneFitResult.Success) return false;
            double md = 0.0;
            foreach (var p in pts) { double d = Math.Abs(plane.DistanceTo(p)); if (d > md) md = d; }
            maxDev = md;
            return md <= Math.Max(10.0 * tol, 0.001 * size);
        }
        catch { maxDev = -1.0; return false; }
    }

    private Vector3d? DiagFaceNormalAt(Brep brep, BrepEdge edge, Point3d pt)
    {
        try
        {
            var faces = edge.AdjacentFaces();
            if (faces == null || faces.Length == 0) return null;
            var face = brep.Faces[faces[0]];
            double u, v;
            if (!face.ClosestPoint(pt, out u, out v)) return null;
            var n = face.NormalAt(u, v);
            if (face.OrientationIsReversed) n = -n;
            return n;
        }
        catch { return null; }
    }

    private double? DiagNormalAngleDeg(DiagEdge eA, DiagEdge eB)
    {
        var angs = new List<double>();
        foreach (var t in DiagSampleParams(eA.Curve, DiagNormalSamples))
        {
            var pA = eA.Curve.PointAt(t);
            var nA = DiagFaceNormalAt(eA.Brep, eA.Edge, pA);
            if (nA == null) continue;
            double tt;
            if (!eB.Curve.ClosestPoint(pA, out tt)) continue;
            var nB = DiagFaceNormalAt(eB.Brep, eB.Edge, eB.Curve.PointAt(tt));
            if (nB == null) continue;
            double deg = Vector3d.VectorAngle(nA.Value, nB.Value) * 180.0 / Math.PI;
            angs.Add(Math.Min(deg, 180.0 - deg));
        }
        if (angs.Count == 0) return null;
        return angs.Average();
    }

    private void DiagClassify(bool bothNaked, DiagGap gap, DiagEnds ends, double tol, out string diagnosis, out string cont)
    {
        if (gap == null) { diagnosis = "not_a_gap"; cont = "G-1"; return; }
        double maxGap = gap.Max;
        if (!bothNaked) { diagnosis = "not_a_gap"; cont = maxGap <= tol ? "G0" : "G-1"; return; }
        if (maxGap <= tol) { diagnosis = "unjoined_coincident"; cont = "G0"; return; }

        bool endsOk = ends.EndGapAvg <= Math.Max(2.0 * tol, 0.5 * maxGap);
        bool staggered = (Math.Min(ends.EndGaps[0], ends.EndGaps[1]) <= tol) && (maxGap > 5.0 * tol);
        if (gap.Uniform && endsOk && !staggered) { diagnosis = "gap"; cont = "G-1"; return; }
        diagnosis = "misalignment"; cont = "G-1";
    }

    private JArray DiagRoadmaps(string diagnosis, DiagGap gap, bool nearPl, string boundary, bool bothNaked)
    {
        var list = new List<JObject>();
        if (diagnosis == "gap")
        {
            bool twoEdge = boundary == "two_edge";
            bool uniform = gap != null ? gap.Uniform : true;
            list.Add(DiagRoadmap("BlendSrf(兩 naked edge 間建混成面)", "G2", twoEdge ? 0.8 : 0.5,
                new[] {
                    "確認兩邊皆 naked、為待橋接的兩自由邊",
                    "_BlendSrf 兩端各選一邊;連續性兩端設 G2(曲率)或 G1(相切)依鄰面需求",
                    "調 bulge / 加斷面控制避免起伏",
                    "驗證點:橋面兩端 _GCon 檢查連續性;法線方向 / _SelfIntersect 檢查"
                },
                "兩條邊、需高連續性銜接時 BlendSrf 最直接,可達相切/曲率連續。"));
            list.Add(DiagRoadmap("Loft(以兩邊為斷面放樣)", nearPl ? "G1" : "G0", (nearPl && twoEdge) ? 0.7 : 0.45,
                new[] {
                    "_Loft 選兩條邊為斷面",
                    "Loft 型式 Normal;必要時 Straight sections",
                    "對齊 seam 避免扭轉",
                    "驗證點:_GCon 兩端;檢查扭轉 / 自交"
                },
                "近平面、長度相近、只需 G0/G1 時 Loft 簡單穩定。"));
            list.Add(DiagRoadmap("sweep2_with_blend_sections(雙軌 + 混成斷面)", "G1", (!uniform || !nearPl) ? 0.6 : 0.4,
                new[] {
                    "在兩 naked edge 間數個取樣位置生成兩端接順的 section 曲線(以鄰面相切方向起收)",
                    "以相對兩邊為雙軌(rails)、sections 為斷面執行 _Sweep2",
                    "驗證點:橋面兩端連續性(_GCon)與是否自交(_SelfIntersect)"
                },
                "間距不均勻或非平面、需沿程控制斷面形狀時,雙軌掃掠最可控。"));
        }
        else if (diagnosis == "unjoined_coincident")
        {
            list.Add(DiagRoadmap("Join(接合應接未接的重合邊)", "G0+", 0.9,
                new[] {
                    "確認兩邊最大間距 ≤ ModelAbsoluteTolerance(本診斷已確認)",
                    "選兩母面 / 兩 brep 執行 _Join",
                    "驗證點:接合後該邊 Valence 由 naked 變 interior(naked 邊數下降);_ShowEdges 確認"
                },
                "兩 naked 邊在公差內重合卻未接合;這是接合問題,不需造面。"));
            list.Add(DiagRoadmap("調公差後再 Join(間距略大於現有公差時)", "G0+", 0.4,
                new[] {
                    "若 _Join 無效,檢查實際間距是否略大於 ModelAbsoluteTolerance",
                    "用 _JoinEdge 指定容差或暫時放寬文件公差(注意副作用),接合後復原",
                    "驗證點:naked 邊數下降;公差已復原"
                },
                "重合卻 Join 不動,常差在公差設定。"));
        }
        else if (diagnosis == "misalignment")
        {
            list.Add(DiagRoadmap("先定關係再處理(對齊 vs 橋接)", "N/A", 0.5,
                new[] {
                    "判定兩邊原意:應重合對齊?還是刻意留縫待橋接?",
                    "若應對齊:_MatchSrf / 移動母面使兩邊吻合,再 _Join",
                    "若刻意留縫且間距不均勻:優先 sweep2_with_blend_sections",
                    "驗證點:對齊後 _GCon;橋接後兩端連續性與自交檢查"
                },
                "間距非均勻 / 端點對不上,直接造面易扭轉;需先確立兩邊關係。"));
        }
        else // not_a_gap
        {
            if (!bothNaked)
                list.Add(DiagRoadmap("不橋接(至少一邊非 naked)", "N/A", 0.9,
                    new[] {
                        "確認非 naked 邊的 Valence(interior=已雙面;nonmanifold=已≥3面)",
                        "若仍想接面,先確認不會造成 non-manifold",
                        "驗證點:不需動作;這不是兩自由邊之間的縫"
                    },
                    "非 naked 邊已被面共用,於此造面 / 接合會造成 non-manifold。"));
        }

        var sorted = list.OrderByDescending(r => (double)r["confidence"]).ToList();
        var arr = new JArray();
        foreach (var r in sorted) arr.Add(r);
        return arr;
    }

    private JObject DiagRoadmap(string strategy, string continuity, double confidence, string[] steps, string why)
    {
        var stepsJson = new JArray();
        foreach (var s in steps) stepsJson.Add(s);
        return new JObject
        {
            ["strategy"] = strategy,
            ["continuity"] = continuity,
            ["confidence"] = confidence,
            ["steps"] = stepsJson,
            ["why"] = why
        };
    }
}
