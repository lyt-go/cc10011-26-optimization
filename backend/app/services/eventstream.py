"""用户事件流：确定性示例事件源 + 高基数分组下的游标分批读取。

要点：
- 事件流用固定随机种子生成，服务重启后内容一致，归因结果可复算、可对比；
- 流里刻意保留少量重复投递（同一 事件ID 多次到达），读取侧按 事件ID 去重，
  保证分批读取不重不漏；
- 分批读取用 (事件时间, 事件ID) 做 keyset 游标，不用 offset 翻页：
  高基数分组下即使读取期间有新事件到达，也不会重复或漏算。
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from typing import Any

# 高基数分组维度：渠道取值多，模拟高基数场景；其中「线下」组量级很小，
# 用来覆盖样本不足的判定路径。
CHANNELS: list[str] = (
    [f"SEM-引擎{i:02d}" for i in range(1, 25)]
    + [f"社交-平台{i:02d}" for i in range(1, 25)]
    + [f"信息流-媒体{i:02d}" for i in range(1, 25)]
    + [f"自然-来源{i:02d}" for i in range(1, 13)]
    + [f"线下-门店{i:02d}" for i in range(1, 5)]
)

TOUCH_TYPES = ("曝光", "点击")
CONVERSION_TYPE = "转化"

STREAM_START = datetime(2026, 11, 1, 0, 0, 0)
STREAM_DAYS = 14

# 同一事件可能命中多套分组规则时的冲突裁决策略：一个归因快照内只钉住
# 最新有效规则版本，全快照一致适用，保证置信区间基于同一归因快照。
CONFLICT_POLICY = "最新有效规则优先"


def _channel_seed(channel: str) -> int:
    return int(hashlib.sha256(channel.encode("utf-8")).hexdigest()[:8], 16)


def _build_stream() -> list[dict[str, Any]]:
    """生成确定性事件流（到达顺序），并注入约 3% 的重复投递。"""
    rng = random.Random(20261101)
    events: list[dict[str, Any]] = []
    seq = 0
    user_seq = 0
    for day in range(STREAM_DAYS):
        day_start = STREAM_START + timedelta(days=day)
        batch_key = day_start.strftime("%Y-%m-%d")
        for channel in CHANNELS:
            seed = _channel_seed(channel)
            if channel.startswith("线下-"):
                users_today = seed % 2  # 0~1 人/天，窗口归并后仍样本不足
            else:
                users_today = 3 + seed % 16  # 3~18 人/天
            conv_prob = 0.08 + (seed % 23) / 100.0  # 0.08~0.30
            for _ in range(users_today):
                user_seq += 1
                user_id = f"U{user_seq:06d}"
                t0 = day_start + timedelta(minutes=rng.randint(0, 14 * 60))
                seq += 1
                events.append({
                    "事件ID": f"EVT-{seq:06d}",
                    "用户ID": user_id,
                    "事件类型": "曝光",
                    "事件时间": t0,
                    "渠道": channel,
                    "业务批次": batch_key,
                })
                last_touch = t0
                if rng.random() < 0.4:
                    t1 = t0 + timedelta(minutes=rng.randint(1, 30))
                    seq += 1
                    events.append({
                        "事件ID": f"EVT-{seq:06d}",
                        "用户ID": user_id,
                        "事件类型": "点击",
                        "事件时间": t1,
                        "渠道": channel,
                        "业务批次": batch_key,
                    })
                    last_touch = t1
                if rng.random() < conv_prob:
                    # 转化可能延迟 0~2 天，检验归因窗口的归并口径
                    t2 = last_touch + timedelta(
                        hours=rng.randint(1, 48), minutes=rng.randint(0, 59)
                    )
                    seq += 1
                    events.append({
                        "事件ID": f"EVT-{seq:06d}",
                        "用户ID": user_id,
                        "事件类型": "转化",
                        "事件时间": t2,
                        "渠道": channel,
                        "业务批次": t2.strftime("%Y-%m-%d"),
                    })
                # 约 15% 的用户在另一个主流渠道也有触点，检验末次触点归因；
                # 跨渠道触点不落在「线下」上，保持稀有渠道真实样本不足
                if rng.random() < 0.15 and not channel.startswith("线下-"):
                    other = CHANNELS[rng.randint(0, len(CHANNELS) - 1)]
                    if other != channel and not other.startswith("线下-"):
                        t3 = last_touch + timedelta(minutes=rng.randint(5, 120))
                        seq += 1
                        events.append({
                            "事件ID": f"EVT-{seq:06d}",
                            "用户ID": user_id,
                            "事件类型": "曝光",
                            "事件时间": t3,
                            "渠道": other,
                            "业务批次": batch_key,
                        })
    # 注入重复投递：同一 事件ID 在流中再次出现（到达顺序更靠后）
    raw_count = len(events)
    dup_count = raw_count // 33
    for _ in range(dup_count):
        src = events[rng.randint(0, raw_count - 1)]
        events.insert(rng.randint(0, len(events)), dict(src))
    return events


class EventStream:
    """事件流读取入口：keyset 游标分批读，按 事件ID 去重。"""

    def __init__(self) -> None:
        self._arrival: list[dict[str, Any]] = _build_stream()

    def batch_keys(self) -> list[str]:
        """事件流覆盖的业务批次（按日期）。"""
        keys = {str(e["业务批次"]) for e in self._arrival}
        return sorted(keys)

    def latest_batch(self) -> str:
        return self.batch_keys()[-1]

    def _dedup_sorted(self, since: datetime, until: datetime) -> tuple[list[dict[str, Any]], int, int]:
        """取 [since, until) 内事件：按 事件ID 去重（首次到达为准），再按
        (事件时间, 事件ID) 排序。返回 (有序事件, 原始条数, 重复剔除数)。"""
        seen: set[str] = set()
        kept: list[dict[str, Any]] = []
        raw = 0
        for event in self._arrival:
            ts = event["事件时间"]
            if not (since <= ts < until):
                continue
            raw += 1
            event_id = str(event["事件ID"])
            if event_id in seen:
                continue
            seen.add(event_id)
            kept.append(event)
        kept.sort(key=lambda e: (e["事件时间"], str(e["事件ID"])))
        return kept, raw, raw - len(kept)

    def read_batch(
        self,
        *,
        since: datetime,
        until: datetime,
        cursor: tuple[datetime, str] | None = None,
        size: int = 500,
    ) -> tuple[list[dict[str, Any]], tuple[datetime, str] | None, bool, dict[str, int]]:
        """按 keyset 游标读一批事件。

        返回 (本批事件, 下一游标, 是否读完, 统计)。游标是上一批最后一条的
        (事件时间, 事件ID)，严格大于游标的才进入下一批，因此：
        - 不重复：游标之前的事件不会再次出现；
        - 不漏算：游标之后的事件按序全量覆盖；
        - 不受读取期间新到事件影响：排序键不可变。
        """
        ordered, raw, dropped = self._dedup_sorted(since, until)
        start = 0
        if cursor is not None:
            cur_time, cur_id = cursor
            while start < len(ordered) and (
                ordered[start]["事件时间"], str(ordered[start]["事件ID"])
            ) <= (cur_time, cur_id):
                start += 1
        page = ordered[start:start + size]
        done = start + size >= len(ordered)
        next_cursor = None
        if page:
            last = page[-1]
            next_cursor = (last["事件时间"], str(last["事件ID"]))
        stats = {"原始事件数": raw, "去重后事件数": len(ordered), "重复剔除数": dropped}
        return page, next_cursor, done, stats


stream = EventStream()
