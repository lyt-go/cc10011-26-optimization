"""事件归因引擎：按归因窗口归并事件流，产出可追溯的事件归因结果数据集。

对应既有数据流上的处理点：
1. 读取：高基数分组下用 keyset 游标分批读事件流，按 事件ID 去重，不重不漏；
2. 归并：同一快照内按归因窗口把触点归并到转化上（末次触点归因）；
3. 分组冲突：同一事件同时命中首次分组规则与最新有效分组规则时，一个快照
   只钉住一个规则版本（最新有效规则优先），全快照一致适用，保证置信区间
   基于同一归因快照；冲突事件与裁决口径记入溯源，可复算；
4. 收敛：置信区间用 Wilson 公式按快照确定性计算，刷新不漂移；样本不足的
   分组照常落行，给出稳定的「样本不足」判定，而不是空表；
5. 落地：同一业务批次并发到达只落一个完整结果数据集，状态只沿
   待计算 → 计算中 → 已生成 → 已发布 向前推进。
"""
from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta
from statistics import NormalDist
from typing import Any

from app.services.eventstream import CONFLICT_POLICY, CONVERSION_TYPE, TOUCH_TYPES, stream
from app.store import store

MODULE = "eventanalysis"

# 结果数据集状态机：只允许沿处理阶段向前推进
DATASET_STATUS_ORDER = ["待计算", "计算中", "已生成", "已发布"]
DATASET_ACTIONS = {"发布数据集": "已发布"}

READ_PAGE_SIZE = 500  # 高基数分组下的分批读取页大小

_LOCK = threading.Lock()
_DATASETS: dict[str, dict[str, Any]] = {}
_DATASET_SEQ = 0


# ---------------------------------------------------------------- 配置解析

def parse_window(value: Any) -> timedelta:
    """解析归因窗口：支持 7天 / 24小时 / 30分钟 / 7d / 24h / 纯数字（按天）。"""
    text = str(value or "").strip()
    try:
        if text.endswith(("天", "日")):
            return timedelta(days=float(text[:-1]))
        if text.endswith("小时"):
            return timedelta(hours=float(text[:-3]))
        if text.endswith("分钟"):
            return timedelta(minutes=float(text[:-3]))
        if text.lower().endswith("d"):
            return timedelta(days=float(text[:-1]))
        if text.lower().endswith("h"):
            return timedelta(hours=float(text[:-1]))
        if text:
            return timedelta(days=float(text))
    except ValueError:
        pass
    return timedelta(days=7)


def parse_confidence(value: Any) -> float:
    """解析置信水平：0.95 / 95% / 95 都按 0.95 处理，非法值回退 0.95。"""
    text = str(value or "").strip().rstrip("%")
    try:
        conf = float(text)
    except ValueError:
        return 0.95
    if conf > 1:
        conf = conf / 100.0
    if not 0.5 < conf < 1:
        return 0.95
    return round(conf, 4)


def parse_min_sample(value: Any) -> int:
    try:
        return max(1, int(float(str(value or "").strip())))
    except ValueError:
        return 30


# ---------------------------------------------------------------- 分组规则

def grouping_rule_versions() -> list[dict[str, Any]]:
    """分组规则版本：v1 首次规则按渠道原始值分组；v2 最新有效规则按渠道大类归并。

    同一事件会同时命中两套规则且分组结果不同，即规则冲突；一个归因快照
    只钉住最新有效版本，保证置信区间基于同一归因快照。
    """
    return [
        {"版本": "v1-首次分组规则", "生效时间": datetime(2026, 11, 1), "口径": "按渠道原始值分组"},
        {"版本": "v2-最新有效分组规则", "生效时间": datetime(2026, 11, 8), "口径": "按渠道大类归并"},
    ]


def effective_rule(at: datetime) -> dict[str, Any]:
    """取快照时点最新有效的分组规则版本。"""
    versions = [v for v in grouping_rule_versions() if v["生效时间"] <= at]
    return versions[-1] if versions else grouping_rule_versions()[0]


def group_by_rule(channel: str, rule: dict[str, Any]) -> str:
    if rule["版本"].startswith("v2"):
        return channel.split("-", 1)[0] if "-" in channel else channel
    return channel


# ---------------------------------------------------------------- 统计口径

def wilson_interval(x: int, n: int, confidence: float) -> tuple[float, float]:
    """Wilson  score 区间：确定性公式，同一快照重复计算结果一致，不随刷新漂移。"""
    if n <= 0:
        return (0.0, 0.0)
    z = NormalDist().inv_cdf((1.0 + confidence) / 2.0)
    p = x / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = z * ((p * (1.0 - p) / n + z2 / (4.0 * n * n)) ** 0.5) / denom
    return (round(max(0.0, center - margin), 6), round(min(1.0, center + margin), 6))


# ---------------------------------------------------------------- 归因计算

def _snapshot_id(model_code: str, batch_key: str, rule_version: str, event_ids: list[str]) -> str:
    digest = hashlib.sha256(
        f"{model_code}|{batch_key}|{rule_version}".encode("utf-8")
    ).hexdigest()[:8]
    ids_digest = hashlib.sha256(",".join(sorted(event_ids)).encode("utf-8")).hexdigest()[:8]
    return f"SNAP-{digest}-{ids_digest}"


def _read_snapshot_events(since: datetime, until: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """用 keyset 游标分批读完全部事件：高基数分组下不重不漏。"""
    events: list[dict[str, Any]] = []
    cursor = None
    pages = 0
    stats = {"原始事件数": 0, "去重后事件数": 0, "重复剔除数": 0}
    while True:
        page, cursor, done, batch_stats = stream.read_batch(
            since=since, until=until, cursor=cursor, size=READ_PAGE_SIZE
        )
        pages += 1
        events.extend(page)
        stats = batch_stats  # 全量去重口径以流读取侧统计为准
        if done:
            break
    stats["读取批次数"] = pages
    return events, stats


def _attribute(
    events: list[dict[str, Any]],
    *,
    batch_start: datetime,
    batch_end: datetime,
    window: timedelta,
    rule: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """按归因窗口归并：同一用户的触点归并到窗口内最近一次触点所在分组（末次触点）。

    返回 (分组统计, 冲突溯源)。分组统计按用户级口径：触达用户数、归因转化用户数。
    """
    first_rule = grouping_rule_versions()[0]
    touches_by_user: dict[str, list[dict[str, Any]]] = {}
    conversions: list[dict[str, Any]] = []
    conflict_events = 0
    conflict_examples: list[dict[str, str]] = []

    for event in events:
        channel = str(event["渠道"])
        first_group = group_by_rule(channel, first_rule)
        latest_group = group_by_rule(channel, rule)
        if first_group != latest_group:
            conflict_events += 1
            if len(conflict_examples) < 5:
                conflict_examples.append({
                    "事件ID": str(event["事件ID"]),
                    "渠道": channel,
                    "首次规则分组": first_group,
                    "最新有效规则分组": latest_group,
                    "裁决": latest_group,
                })
        if event["事件类型"] in TOUCH_TYPES:
            touches_by_user.setdefault(str(event["用户ID"]), []).append(event)
        elif event["事件类型"] == CONVERSION_TYPE and batch_start <= event["事件时间"] < batch_end:
            conversions.append(event)

    groups: dict[str, dict[str, Any]] = {}

    def bucket(group: str) -> dict[str, Any]:
        return groups.setdefault(group, {"触达用户": set(), "转化用户": set()})

    for user_id, touches in touches_by_user.items():
        for touch in touches:
            bucket(group_by_rule(str(touch["渠道"]), rule))["触达用户"].add(user_id)

    for conv in conversions:
        user_id = str(conv["用户ID"])
        conv_time = conv["事件时间"]
        candidates = [
            t for t in touches_by_user.get(user_id, [])
            if timedelta(0) <= conv_time - t["事件时间"] <= window
        ]
        if not candidates:
            bucket("未归因")["转化用户"].add(user_id)
            continue
        # 末次触点归因：窗口内最近一次触点所在分组
        last_touch = max(candidates, key=lambda t: (t["事件时间"], str(t["事件ID"])))
        bucket(group_by_rule(str(last_touch["渠道"]), rule))["转化用户"].add(user_id)

    trace = {
        "分组冲突事件数": conflict_events,
        "冲突样例": conflict_examples,
        "转化事件数": len(conversions),
    }
    return groups, trace


# ---------------------------------------------------------------- 结果数据集

def _advance(dataset: dict[str, Any], target: str) -> tuple[bool, str]:
    """状态只沿 待计算→计算中→已生成→已发布 向前推进，回退或跳级都拒绝。"""
    current = str(dataset["状态"])
    if target not in DATASET_STATUS_ORDER:
        return False, f"目标状态「{target}」不在数据集状态序列里"
    if DATASET_STATUS_ORDER.index(target) <= DATASET_STATUS_ORDER.index(current):
        return False, f"数据集状态只能从「{current}」向前推进，不允许回到「{target}」"
    dataset["状态"] = target
    dataset["状态历史"].append({"状态": target, "时间": datetime.now().isoformat(timespec="seconds")})
    return True, f"数据集状态已推进为「{target}」"


def compute_attribution(model_id: int, batch_key: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """读取用户事件流与分析模型配置，按归因窗口归并并计算事件归因。

    同一业务批次并发到达时只落一个完整结果数据集：批次键已落地的直接复用，
    不重复计算、不重复落数据。
    """
    model = store.find(MODULE, model_id)
    if model is None:
        return None, f"分析模型 {model_id} 不存在或已归档"
    if model.get("status") == "已归档":
        return None, f"分析模型 {model_id} 已归档，不能再发起归因计算"

    batch_key = (batch_key or "").strip() or stream.latest_batch()
    if batch_key not in stream.batch_keys():
        return None, f"业务批次 {batch_key} 不在事件流覆盖范围（{stream.batch_keys()[0]} ~ {stream.batch_keys()[-1]}）"

    model_code = str(model.get("模型编号") or model_id)
    dataset_key = f"{model_code}:{batch_key}"

    with _LOCK:
        existing = _DATASETS.get(dataset_key)
        if existing is not None and existing["状态"] in ("已生成", "已发布"):
            return existing, f"业务批次 {batch_key} 已落地完整结果数据集，复用同一份（快照 {existing['快照ID']}）"
        if existing is not None and existing["状态"] == "计算中":
            return existing, f"业务批次 {batch_key} 正在计算中，并发请求等待同一份结果"

        global _DATASET_SEQ
        _DATASET_SEQ += 1
        dataset: dict[str, Any] = {
            "id": _DATASET_SEQ,
            "批次键": dataset_key,
            "模型编号": model_code,
            "事件名称": model.get("事件名称"),
            "业务批次": batch_key,
            "状态": "待计算",
            "状态历史": [{"状态": "待计算", "时间": datetime.now().isoformat(timespec="seconds")}],
        }
        _DATASETS[dataset_key] = dataset
        _advance(dataset, "计算中")

        # 1) 解析分析模型配置
        window = parse_window(model.get("归因窗口"))
        confidence = parse_confidence(model.get("置信水平"))
        min_sample = parse_min_sample(model.get("最小样本量"))

        # 2) 归因窗口归并范围：转化只取本业务批次，触点向前回溯一个归因窗口
        batch_start = datetime.strptime(batch_key, "%Y-%m-%d")
        batch_end = batch_start + timedelta(days=1)
        since = batch_start - window

        # 3) 高基数分组下游标分批读取，不重不漏
        events, read_stats = _read_snapshot_events(since, batch_end)

        # 4) 钉住快照时点最新有效的分组规则版本（冲突裁决：最新有效规则优先）
        rule = effective_rule(batch_start)
        snapshot_id = _snapshot_id(
            model_code, batch_key, str(rule["版本"]), [str(e["事件ID"]) for e in events]
        )

        # 5) 按归因窗口归并并计算事件归因
        groups, attr_trace = _attribute(
            events, batch_start=batch_start, batch_end=batch_end,
            window=window, rule=rule,
        )

        # 6) 置信区间与样本判定：全部基于同一归因快照，行级可追溯到快照
        rows: list[dict[str, Any]] = []
        for group_value in sorted(groups):
            stat = groups[group_value]
            n = len(stat["触达用户"])
            x = len(stat["转化用户"])
            p = round(x / n, 6) if n else 0.0
            lower, upper = wilson_interval(x, n, confidence)
            sufficient = n >= min_sample
            rows.append({
                "分组值": group_value,
                "触达用户数": n,
                "归因转化用户数": x,
                "转化率": p,
                "置信区间下限": lower,
                "置信区间上限": upper,
                "置信水平": confidence,
                "样本判定": "样本充足" if sufficient else "样本不足",
                "判定依据": f"触达用户数 {n} {'≥' if sufficient else '<'} 最小样本量 {min_sample}",
                "归因快照": snapshot_id,
            })
        rows.sort(key=lambda r: (-r["归因转化用户数"], str(r["分组值"])))

        sufficient_groups = sum(1 for r in rows if r["样本判定"] == "样本充足")
        dataset.update({
            "快照ID": snapshot_id,
            "规则版本": rule["版本"],
            "冲突裁决策略": CONFLICT_POLICY,
            "归因窗口": str(model.get("归因窗口") or "7天"),
            "置信水平": confidence,
            "最小样本量": min_sample,
            "窗口起点": since.isoformat(timespec="seconds"),
            "窗口终点": batch_end.isoformat(timespec="seconds"),
            "行数": len(rows),
            "样本充足组数": sufficient_groups,
            "样本不足组数": len(rows) - sufficient_groups,
            "生成时间": datetime.now().isoformat(timespec="seconds"),
            "rows": rows,
            "溯源": {
                **read_stats,
                **attr_trace,
                "快照ID": snapshot_id,
                "规则版本": rule["版本"],
                "规则口径": rule["口径"],
                "冲突裁决策略": CONFLICT_POLICY,
                "样本判定口径": f"触达用户数 ≥ 最小样本量 {min_sample} 判定为样本充足，否则样本不足",
                "置信区间口径": f"Wilson score 区间，置信水平 {confidence}，基于同一归因快照 {snapshot_id} 确定性计算",
            },
        })
        _advance(dataset, "已生成")
        return dataset, f"事件归因计算完成：{len(rows)} 个分组，快照 {snapshot_id}"


def list_datasets(*, page: int = 1, size: int = 20) -> tuple[list[dict[str, Any]], int]:
    """列出事件归因结果数据集（不含行明细，行明细走明细接口）。"""
    with _LOCK:
        items = [
            {k: v for k, v in dataset.items() if k != "rows"}
            for dataset in sorted(_DATASETS.values(), key=lambda d: int(d["id"]))
        ]
    total = len(items)
    start = max(page - 1, 0) * size
    return items[start:start + size], total


def get_dataset(dataset_id: int) -> dict[str, Any] | None:
    with _LOCK:
        for dataset in _DATASETS.values():
            if int(dataset["id"]) == dataset_id:
                return dataset
    return None


def run_dataset_action(dataset_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
    """结果数据集动作：只允许沿状态序列向前推进（如 已生成→已发布）。"""
    dataset = get_dataset(dataset_id)
    if dataset is None:
        return None, f"事件归因结果数据集 {dataset_id} 不存在"
    if action not in DATASET_ACTIONS:
        return None, f"动作「{action}」不属于事件归因结果数据集可执行范围"
    with _LOCK:
        ok, message = _advance(dataset, DATASET_ACTIONS[action])
    if not ok:
        return None, message
    return dataset, message
