# -*- coding: utf-8 -*-
"""
diagnose_edge_pair.py
=====================
Rhino 幾何診斷眼睛 — 第一顆:診斷「兩條曲面邊緣」的關係。

做什麼:讀當前選取的兩條 brep 邊子物件 → 量測 → 分類 → 排序修補 roadmap → 出警告。
分類:gap / misalignment / unjoined_coincident / not_a_gap。
**只診斷,不修補。** 腳本只讀選取與 in-memory 幾何複本,絕不寫回文件。

怎麼跑(基底現成工具,Phase 1 唯一依賴):
    透過 MCP 工具 `execute_rhinoscript_python_code`,把本檔整段貼進 `code` 參數執行。
    在 Rhino 端:先用 Ctrl+Shift 點選「兩條曲面邊緣」(sub-object 子物件選取),再跑。
    執行引擎是 Rhino 內建 IronPython 2.7(PythonScript.Create + SetupScriptContext),
    所以本檔寫成 Python 2.7 相容(無 f-string;from __future__ import division)。
    輸出:本腳本只 print 一個 JSON 物件,進基底回傳契約的 `output` 欄位。

API 查證狀態(鐵則 5:不准憑記憶猜):
    [已對官方文件核對]
      - doc.Objects.GetSelectedObjects(False, False)         -> RhinoObject 列舉(基底已用)
      - RhinoObject.GetSelectedSubObjects()                  -> ComponentIndex[] 或 None(必須 null check)
      - ComponentIndex.ComponentIndexType / .Index           -> 枚舉值 BrepEdge / int
      - BrepEdge.Valence -> Rhino.Geometry.EdgeAdjacency      -> None/Naked/Interior/NonManifold = 0/1/2/3
      - Curve.GetDistancesBetweenCurves(a,b,tol, out x6)      -> IronPython 回 7-tuple
                                                                (ok, maxDist,maxA,maxB, minDist,minA,minB)
    [信心高、尚未逐一核對 -> 標 TODO,靠執行驗證迴圈確認]
      標於各函式內。第一版若 Rhino 報錯屬正常,Louis 貼回報錯後據此修。
"""

from __future__ import division

import json
import math
import traceback

import Rhino
import scriptcontext as sc


# 沿邊取樣點數(含端點)。純量測用,不寫死任何「公差」數字(公差一律取自文件)。
SAMPLES = 21
NORMAL_SAMPLES = 9


# ----------------------------------------------------------------------------- #
# 小工具
# ----------------------------------------------------------------------------- #
def _f(x, nd=8):
    """把 .NET double / 數值安全轉成 round 過的 Python float;失敗回 None。"""
    try:
        return round(float(x), nd)
    except Exception:
        return None


def _neg(v):
    """回傳反向 Vector3d(避免 struct 就地 .Reverse() 在 IronPython 的 boxing 疑慮)。"""
    return Rhino.Geometry.Vector3d(-v.X, -v.Y, -v.Z)


# ----------------------------------------------------------------------------- #
# 選取讀取(鐵則 6:只讀選取,不掃全場景)
# ----------------------------------------------------------------------------- #
def get_brep(obj):
    """從選取的 RhinoObject 取 Brep。回 (brep|None, from_extrusion_bool)。"""
    geo = obj.Geometry
    if isinstance(geo, Rhino.Geometry.Brep):
        return geo, False
    if isinstance(geo, Rhino.Geometry.Extrusion):
        # TODO[驗]: Extrusion.ToBrep() 後的 edge index 不保證對應 sub-object 選取的
        #   ComponentIndex.Index。常見「曲面/多重曲面」是原生 Brep,不走這支。
        #   若 Louis 真選到 extrusion 邊,回報後改走 ObjRef 或先 _ConvertExtrusion。
        return geo.ToBrep(), True
    # TODO[驗]: Brep.TryConvertBrep(GeometryBase) -> Brep | None(標準 API、信心高)。
    return Rhino.Geometry.Brep.TryConvertBrep(geo), False


def valence_str(edge):
    """BrepEdge.Valence -> 'naked'|'interior'|'nonmanifold'|'none'(已核對枚舉)。"""
    v = edge.Valence
    EA = Rhino.Geometry.EdgeAdjacency
    if v == EA.Naked:
        return "naked"
    if v == EA.Interior:
        return "interior"
    if v == EA.NonManifold:
        return "nonmanifold"
    return "none"


def collect_selected_edges(doc):
    """讀當前選取裡所有 brep 邊子物件。回 (edges, notes)。
    edges 每筆: dict(obj, brep, brep_id, index, edge, from_extrusion)
    """
    found = []
    notes = []
    other_selected = 0
    sel = doc.Objects.GetSelectedObjects(False, False)  # [已驗證]
    for obj in sel:
        cis = obj.GetSelectedSubObjects()  # [已驗證] ComponentIndex[] 或 None
        picked_here = 0
        if cis is not None:
            for ci in cis:
                if ci.ComponentIndexType == Rhino.Geometry.ComponentIndexType.BrepEdge:
                    brep, from_ext = get_brep(obj)
                    if brep is None:
                        notes.append("物件 {0} 無法轉成 Brep,略過。".format(str(obj.Id)))
                        continue
                    idx = ci.Index
                    if idx < 0 or idx >= brep.Edges.Count:
                        notes.append("物件 {0} edge index {1} 超出範圍,略過。".format(str(obj.Id), idx))
                        continue
                    found.append(dict(obj=obj, brep=brep, brep_id=str(obj.Id),
                                      index=idx, edge=brep.Edges[idx], from_extrusion=from_ext))
                    picked_here += 1
                    if from_ext:
                        notes.append("物件 {0} 為 Extrusion,邊索引對應未保證(見腳本 TODO)。".format(str(obj.Id)))
        if picked_here == 0:
            other_selected += 1
    if other_selected:
        notes.append("另有 {0} 個選取物件非 brep 邊子物件(可能整體選取或獨立曲線);本眼只看 brep 邊子物件。".format(other_selected))
    return found, notes


# ----------------------------------------------------------------------------- #
# 量測
# ----------------------------------------------------------------------------- #
def sample_params(crv, n):
    """沿弧長取 n 個參數(含端點)。回 list[float]。"""
    # TODO[驗]: Curve.DivideByCount(segmentCount, includeEnds) -> double[] | None。
    ts = crv.DivideByCount(n - 1, True)
    if ts is None:
        dom = crv.Domain
        return [dom.ParameterAt(i / (n - 1)) for i in range(n)]
    return list(ts)


def gap_metrics(cA, cB, tol):
    """雙向沿邊取樣 + 官方 GetDistancesBetweenCurves 交叉檢查,求 max/mean/min/uniform。"""
    dists = []
    for src, dst in ((cA, cB), (cB, cA)):
        for t in sample_params(src, SAMPLES):
            p = src.PointAt(t)
            rc = dst.ClosestPoint(p)  # TODO[驗]: Curve.ClosestPoint(pt, out t) -> (bool, t)
            if rc[0]:
                dists.append(p.DistanceTo(dst.PointAt(rc[1])))

    dev = Rhino.Geometry.Curve.GetDistancesBetweenCurves(cA, cB, tol)  # [已驗證] 7-tuple
    dev_ok = bool(dev[0])

    if not dists and not dev_ok:
        return None

    if dists:
        mx, mn, mean = max(dists), min(dists), sum(dists) / len(dists)
    else:
        mx = mn = mean = dev[1]
    if dev_ok:
        mx = max(mx, dev[1])
        mn = min(mn, dev[4])

    span = mx - mn
    if mean > 0:
        uniform = span <= max(2.0 * tol, 0.10 * mean)
    else:
        uniform = span <= 2.0 * tol
    return dict(max=mx, mean=mean, min=mn, span=span, uniform=bool(uniform),
                n=len(dists), dev_ok=dev_ok)


def endpoint_pairing(cA, cB):
    """兩邊端點以最佳配對求端隙;回 dict(end_gap_avg, end_gaps, reversed)。"""
    a0, a1 = cA.PointAtStart, cA.PointAtEnd
    b0, b1 = cB.PointAtStart, cB.PointAtEnd
    straight = a0.DistanceTo(b0) + a1.DistanceTo(b1)
    crossed = a0.DistanceTo(b1) + a1.DistanceTo(b0)
    if straight <= crossed:
        end_gaps = [a0.DistanceTo(b0), a1.DistanceTo(b1)]
    else:
        end_gaps = [a0.DistanceTo(b1), a1.DistanceTo(b0)]
    return dict(end_gap_avg=sum(end_gaps) / 2.0, end_gaps=end_gaps,
                reversed=bool(crossed < straight))


def near_planar(curves, tol):
    """兩邊取樣點 best-fit plane,最大偏差 ≤ 門檻 → near_planar。回 (bool, max_dev|None)。
    純量測、in-memory。失敗一律回 (False, None),不讓它擋住主流程。"""
    try:
        from System.Collections.Generic import List
        pts = List[Rhino.Geometry.Point3d]()
        size = 0.0
        for c in curves:
            size = max(size, c.GetLength())
            for t in sample_params(c, SAMPLES):
                pts.Add(c.PointAt(t))
        # TODO[驗]: Plane.FitPlaneToPoints(IEnumerable<Point3d>) -> (PlaneFitResult, Plane)。
        rc = Rhino.Geometry.Plane.FitPlaneToPoints(pts)
        if str(rc[0]) != "Success":
            return False, None
        plane = rc[1]
        max_dev = 0.0
        for p in pts:
            d = abs(plane.DistanceTo(p))  # TODO[驗]: Plane.DistanceTo(Point3d) -> signed double
            if d > max_dev:
                max_dev = d
        return (max_dev <= max(10.0 * tol, 0.001 * size)), max_dev
    except Exception:
        return False, None


def face_normal_at(brep, edge, pt):
    """naked 邊的相鄰面在最近點的外法線(已修正面反轉)。回 Vector3d|None。"""
    try:
        # TODO[驗]: BrepEdge.AdjacentFaces() -> int[]
        faces = edge.AdjacentFaces()
        if faces is None or len(faces) == 0:
            return None
        face = brep.Faces[faces[0]]
        rc = face.ClosestPoint(pt)  # TODO[驗]: BrepFace.ClosestPoint(pt, out u, out v) -> (bool,u,v)
        if not rc[0]:
            return None
        n = face.NormalAt(rc[1], rc[2])
        if face.OrientationIsReversed:  # TODO[驗]: BrepFace.OrientationIsReversed -> bool
            n = _neg(n)
        return n
    except Exception:
        return None


def normal_angle_deg(eA, eB, cA, cB):
    """沿 A 取樣,比兩邊相鄰面法線夾角(orientation-agnostic),回平均度數|None。
    粗略 G1 指標(鐵則:別卡死在連續性)。"""
    angs = []
    for t in sample_params(cA, NORMAL_SAMPLES):
        pA = cA.PointAt(t)
        nA = face_normal_at(eA["brep"], eA["edge"], pA)
        if nA is None:
            continue
        rc = cB.ClosestPoint(pA)
        if not rc[0]:
            continue
        nB = face_normal_at(eB["brep"], eB["edge"], cB.PointAt(rc[1]))
        if nB is None:
            continue
        # TODO[驗]: Vector3d.VectorAngle(a, b) -> 弧度 0..pi
        deg = math.degrees(Rhino.Geometry.Vector3d.VectorAngle(nA, nB))
        angs.append(min(deg, 180.0 - deg))
    if not angs:
        return None
    return sum(angs) / len(angs)


# ----------------------------------------------------------------------------- #
# 判讀(分類 + roadmap)— 啟發式門檻一律相對 tol,公開於 measurements 供核對
# ----------------------------------------------------------------------------- #
def classify(both_naked, gap, ends, tol):
    """回 (diagnosis, continuity_current)。"""
    if gap is None:
        return "not_a_gap", "G-1"
    max_gap = gap["max"]
    if not both_naked:
        # 至少一邊非 naked → 不是兩自由邊之間可橋接的縫(non-manifold 風險由 warnings 帶出)
        return "not_a_gap", ("G0" if max_gap <= tol else "G-1")
    if max_gap <= tol:
        # 整段在公差內 → 重合;兩條仍是各自 naked = 應接未接(不可誤判成 gap)
        return "unjoined_coincident", "G0"
    # max_gap > tol:有真實分離
    ends_ok = ends["end_gap_avg"] <= max(2.0 * tol, 0.5 * max_gap)
    staggered = (min(ends["end_gaps"]) <= tol) and (max_gap > 5.0 * tol)  # 一端貼死、另一端大開
    if gap["uniform"] and ends_ok and not staggered:
        return "gap", "G-1"
    return "misalignment", "G-1"


def roadmaps_for(diagnosis, gap, near_pl, boundary, both_naked):
    """回 roadmap 劇本清單(尚未排序;主流程依 confidence 降序)。"""
    rms = []
    if diagnosis == "gap":
        two_edge = (boundary == "two_edge")
        uniform = gap["uniform"] if gap else True
        rms.append(dict(
            strategy="BlendSrf(兩 naked edge 間建混成面)",
            continuity="G2",
            confidence=0.8 if two_edge else 0.5,
            steps=[
                "確認兩邊皆 naked、為待橋接的兩自由邊",
                "_BlendSrf 兩端各選一邊;連續性兩端設 G2(曲率)或 G1(相切)依鄰面需求",
                "調 bulge / 加斷面控制避免起伏",
                "驗證點:橋面兩端 _GCon 檢查連續性;法線方向 / _SelfIntersect 檢查",
            ],
            why="兩條邊、需高連續性銜接時 BlendSrf 最直接,可達相切/曲率連續。",
        ))
        rms.append(dict(
            strategy="Loft(以兩邊為斷面放樣)",
            continuity="G1" if near_pl else "G0",
            confidence=0.7 if (near_pl and two_edge) else 0.45,
            steps=[
                "_Loft 選兩條邊為斷面",
                "Loft 型式 Normal;必要時 Straight sections",
                "對齊 seam 避免扭轉",
                "驗證點:_GCon 兩端;檢查扭轉 / 自交",
            ],
            why="近平面、長度相近、只需 G0/G1 時 Loft 簡單穩定。",
        ))
        rms.append(dict(
            strategy="sweep2_with_blend_sections(雙軌 + 混成斷面)",
            continuity="G1",
            confidence=0.6 if (not uniform or not near_pl) else 0.4,
            steps=[
                "在兩 naked edge 間數個取樣位置生成兩端接順的 section 曲線(以鄰面相切方向起收)",
                "以相對兩邊為雙軌(rails)、sections 為斷面執行 _Sweep2",
                "驗證點:橋面兩端連續性(_GCon)與是否自交(_SelfIntersect)",
            ],
            why="間距不均勻或非平面、需沿程控制斷面形狀時,雙軌掃掠最可控。",
        ))
    elif diagnosis == "unjoined_coincident":
        rms.append(dict(
            strategy="Join(接合應接未接的重合邊)",
            continuity="G0+",
            confidence=0.9,
            steps=[
                "確認兩邊最大間距 ≤ ModelAbsoluteTolerance(本診斷已確認)",
                "選兩母面 / 兩 brep 執行 _Join",
                "驗證點:接合後該邊 Valence 由 naked 變 interior(naked 邊數下降);_ShowEdges 確認",
            ],
            why="兩 naked 邊在公差內重合卻未接合;這是接合問題,不需造面。",
        ))
        rms.append(dict(
            strategy="調公差後再 Join(間距略大於現有公差時)",
            continuity="G0+",
            confidence=0.4,
            steps=[
                "若 _Join 無效,檢查實際間距是否略大於 ModelAbsoluteTolerance",
                "用 _JoinEdge 指定容差或暫時放寬文件公差(注意副作用),接合後復原",
                "驗證點:naked 邊數下降;公差已復原",
            ],
            why="重合卻 Join 不動,常差在公差設定。",
        ))
    elif diagnosis == "misalignment":
        rms.append(dict(
            strategy="先定關係再處理(對齊 vs 橋接)",
            continuity="N/A",
            confidence=0.5,
            steps=[
                "判定兩邊原意:應重合對齊?還是刻意留縫待橋接?",
                "若應對齊:_MatchSrf / 移動母面使兩邊吻合,再 _Join",
                "若刻意留縫且間距不均勻:優先 sweep2_with_blend_sections",
                "驗證點:對齊後 _GCon;橋接後兩端連續性與自交檢查",
            ],
            why="間距非均勻 / 端點對不上,直接造面易扭轉;需先確立兩邊關係。",
        ))
    else:  # not_a_gap
        if not both_naked:
            rms.append(dict(
                strategy="不橋接(至少一邊非 naked)",
                continuity="N/A",
                confidence=0.9,
                steps=[
                    "確認非 naked 邊的 Valence(interior=已雙面;nonmanifold=已≥3面)",
                    "若仍想接面,先確認不會造成 non-manifold",
                    "驗證點:不需動作;這不是兩自由邊之間的縫",
                ],
                why="非 naked 邊已被面共用,於此造面 / 接合會造成 non-manifold。",
            ))
    return rms


# ----------------------------------------------------------------------------- #
# 主流程
# ----------------------------------------------------------------------------- #
def main():
    doc = sc.doc
    tol = doc.ModelAbsoluteTolerance  # 鐵則 6:公差取自文件,不寫死

    edges, notes = collect_selected_edges(doc)

    if len(edges) != 2:
        print(json.dumps(dict(
            diagnosis="not_a_gap",
            error="需要剛好兩條 brep 邊子物件,目前抓到 {0} 條。".format(len(edges)),
            how_to_select="在 Rhino 用 Ctrl+Shift 點選兩條曲面邊緣(sub-object),再執行本腳本。",
            edges=[], gap=None, continuity_current="G-1",
            boundary_complexity="two_edge", near_planar=False,
            recommended_roadmaps=[], warnings=notes,
            measurements=dict(tolerance=_f(tol)),
        ), ensure_ascii=False))
        return

    A, B = edges[0], edges[1]
    cA = A["edge"].DuplicateCurve()  # in-memory 複本,不改文件
    cB = B["edge"].DuplicateCurve()

    edges_info = []
    for e, c in ((A, cA), (B, cB)):
        vstr = valence_str(e["edge"])
        edges_info.append(dict(
            id="{0}#edge{1}".format(e["brep_id"], e["index"]),
            parent_brep_id=e["parent_brep_id"],
            index=e["index"],
            is_naked=(vstr == "naked"),
            valence=vstr,
            length=_f(c.GetLength()),
            degree=int(c.Degree),
        ))

    both_naked = all(ei["is_naked"] for ei in edges_info)

    gap = gap_metrics(cA, cB, tol)
    ends = endpoint_pairing(cA, cB)
    near_pl, near_dev = near_planar([cA, cB], tol)

    # 邊界複雜度:Phase 1 用端點配對啟發式。
    # TODO[refine]: 嚴謹做法走 naked edge loop 拓樸(brep.Loops / trim loop)判定開口邊數。
    ref = gap["max"] if gap else tol
    boundary = "two_edge" if (ends["end_gap_avg"] <= max(2.0 * tol, 0.5 * ref)) else "multi_edge"

    diagnosis, cont = classify(both_naked, gap, ends, tol)

    # 連續性升級:重合(G0)且法線夾角小 → 粗略 G1
    nang = None
    if gap is not None and gap["max"] <= tol and both_naked:
        nang = normal_angle_deg(A, B, cA, cB)
        if nang is not None and nang < 10.0:
            cont = "G1"

    la, lb = edges_info[0]["length"], edges_info[1]["length"]
    length_ratio = _f(min(la, lb) / max(la, lb), 4) if (la and lb and max(la, lb) > 0) else None

    roadmaps = roadmaps_for(diagnosis, gap, near_pl, boundary, both_naked)
    roadmaps.sort(key=lambda r: r["confidence"], reverse=True)

    # 警告
    warnings = list(notes)
    for ei in edges_info:
        if ei["valence"] != "naked":
            warnings.append("邊 {0}(brep {1})非 naked(valence={2}),在此 join/bridge 會產生 non-manifold。".format(
                ei["index"], ei["parent_brep_id"], ei["valence"]))
    if gap is not None and not gap["uniform"]:
        warnings.append("間距不均勻(max={0} min={1} mean={2}):偏向錯位或非平行縫。".format(
            _f(gap["max"]), _f(gap["min"]), _f(gap["mean"])))
    if length_ratio is not None and length_ratio < 0.5:
        warnings.append("兩邊長度比 {0} 偏小:放樣 / 橋接易扭轉,留意 seam 對齊。".format(length_ratio))
    if A["brep_id"] == B["brep_id"]:
        warnings.append("兩邊屬同一 brep(同物件上的兩條邊)。")
    if gap is not None and not gap.get("dev_ok", True):
        warnings.append("GetDistancesBetweenCurves 回 false,gap 數值改採沿邊取樣;請以此交叉確認。")

    out = dict(
        diagnosis=diagnosis,
        edges=[dict(id=ei["id"], parent_brep_id=ei["parent_brep_id"], index=ei["index"],
                    is_naked=ei["is_naked"], valence=ei["valence"],
                    length=ei["length"], degree=ei["degree"]) for ei in edges_info],
        gap=(dict(max=_f(gap["max"]), mean=_f(gap["mean"]), min=_f(gap["min"]),
                  uniform=gap["uniform"]) if gap else None),
        continuity_current=cont,
        boundary_complexity=boundary,
        near_planar=bool(near_pl),
        recommended_roadmaps=roadmaps,
        warnings=warnings,
        # 附加量測(超出契約,方便核對與迭代)
        measurements=dict(
            tolerance=_f(tol),
            length_ratio=length_ratio,
            gap_span=_f(gap["span"]) if gap else None,
            gap_samples=gap["n"] if gap else 0,
            end_gap_avg=_f(ends["end_gap_avg"]),
            end_gaps=[_f(x) for x in ends["end_gaps"]],
            normal_angle_deg=_f(nang, 3) if nang is not None else None,
            near_planar_max_dev=_f(near_dev) if near_dev is not None else None,
            both_naked=both_naked,
        ),
    )
    print(json.dumps(out, ensure_ascii=False))


# 頂層守衛:任何例外都回成 JSON(ensure_ascii=True 保險),確保 Louis 永遠拿到可解析輸出。
try:
    main()
except Exception as exc:
    print(json.dumps(dict(
        diagnosis="not_a_gap",
        error="腳本執行例外:{0}".format(str(exc)),
        traceback=traceback.format_exc(),
        edges=[], gap=None, continuity_current="G-1",
        boundary_complexity="two_edge", near_planar=False,
        recommended_roadmaps=[], warnings=[],
    ), ensure_ascii=True))
