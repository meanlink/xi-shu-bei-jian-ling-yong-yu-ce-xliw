"""Croston/SBA forecast for intermittent parts issues; no inventory mutations."""
from datetime import date, timedelta
import math
import random
import statistics


def metadata():
    return {
        "name": "稀疏备件领用预测", "short": "spare-croston",
        "tagline": "把零散领用转换成可回测的周度备件协同依据",
        "client": "模拟客户：江湾精机维保组，18 名技师维护 76 台数控机床。",
        "workflow": "技师每晚汇总领用单，计划员在周五用最近七天均值估计下周领用量，再邮件给采购。",
        "pains": ["连续零领用让均值误判需求消失", "一次集中检修拉高短期均值", "预测结果缺少历史回测和参数留痕"],
        "upstream": "ERP 导出的 180 天备件领用日历（包含零需求日）",
        "downstream": "周度维保备料协同会；输出预测用于人工判断，不生成采购单。",
        "entities": ["领用日历", "预测试算", "周度分发计划"],
        "dataset_name": "江湾精机_2026Q3_稀疏领用.csv",
        "columns": [{"key": "day", "label": "领用日", "type": "string"}, {"key": "part", "label": "备件编码", "type": "string"}, {"key": "demand", "label": "领用件数", "type": "number"}],
        "parameters": [
            {"key": "part", "label": "备件", "type": "select", "default": "R17", "options": [{"value": "R17", "label": "R17 密封环"}, {"value": "G22", "label": "G22 联轴片"}]},
            {"key": "alpha", "label": "平滑系数 α", "type": "number", "default": 0.2, "min": 0.05, "max": 0.8},
            {"key": "horizon", "label": "预测天数", "type": "number", "default": 14, "min": 3, "max": 45},
            {"key": "variant", "label": "偏差校正", "type": "select", "default": "sba", "options": [{"value": "sba", "label": "SBA 校正"}, {"value": "croston", "label": "原始 Croston"}]},
        ],
        "workbench_title": "领用间隔与批量试算", "action_label": "运行领用预测", "unit": "件/天",
        "accent": "oklch(54% 0.12 65)", "style": "parts-ledger",
        "seed_story": ["08:30 上传江湾精机_2026Q3_稀疏领用.csv，共 360 行。", "08:40 校验 R17 与 G22 各 180 天日历，零领用日也保留。", "09:00 选择 R17、α=0.2 和 SBA 校正。", "09:10 运行 14 天预测并查看末 28 天逐日回测。", "09:20 比较原始 Croston 与 SBA 的日均预测差异。", "10:00 将累计领用估计提交维保例会，检查停机检修安排。", "16:00 为维保组设置本地收件箱分发计划，归档参数和结果。"],
        "method": "Croston 对非零需求量和需求间隔分别指数平滑，SBA 乘以 (1−α/2)。末 28 天做严格向前滚动一步回测。",
        "limitations": ["预测是假设未来领用过程稳定时的期望值，不是安全库存或服务水平承诺。", "无采购提前期、库存余额和停机计划输入，不输出补货指令。"],
    }


def seed_records():
    rng = random.Random(1015)
    result = []
    start = date(2026, 4, 1)
    for part, probability in (("R17", 0.20), ("G22", 0.12)):
        for index in range(180):
            amount = rng.choice([1, 1, 2, 3, 5]) if rng.random() < probability else 0
            if index in (72, 144):
                amount += 5 if part == "R17" else 2
            result.append({"day": str(start + timedelta(days=index)), "part": part, "demand": amount})
    return result


def _number(value, name, low, high):
    try:
        answer = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{name}必须是数值") from None
    if not math.isfinite(answer) or not low <= answer <= high:
        raise ValueError(f"{name}必须在 {low} 至 {high} 之间")
    return answer


def _rate(values, alpha, sba):
    nonzero = [index for index, value in enumerate(values) if value > 0]
    if not nonzero:
        return 0.0
    first = nonzero[0]
    size = float(values[first])
    gap = float(first + 1)
    last = first
    for index in nonzero[1:]:
        size += alpha * (values[index] - size)
        gap += alpha * ((index - last) - gap)
        last = index
    return size / gap * (1 - alpha / 2 if sba else 1)


def analyze(records, params):
    if not isinstance(records, list) or not records:
        raise ValueError("请导入包含零需求日的领用日历")
    part = str(params.get("part", "R17"))
    alpha = _number(params.get("alpha", 0.2), "α", 0.05, 0.8)
    horizon_value = _number(params.get("horizon", 14), "预测天数", 3, 45)
    if not horizon_value.is_integer():
        raise ValueError("预测天数必须是整数")
    horizon = int(horizon_value)
    variant = params.get("variant", "sba")
    if variant not in ("sba", "croston"):
        raise ValueError("未知的 Croston 变体")
    rows = []
    for record in records:
        if str(record.get("part")) != part:
            continue
        try:
            day = date.fromisoformat(str(record["day"]))
            demand = float(record["demand"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("领用日须为 ISO 日期且 demand 为非负数") from None
        if not math.isfinite(demand) or not 0 <= demand <= 1_000_000_000:
            raise ValueError("单日领用量必须是0至10亿之间的有限数值")
        rows.append((day, demand))
    rows.sort()
    if len(rows) < 45:
        raise ValueError("所选备件至少需要 45 个连续自然日")
    if any((rows[i][0] - rows[i - 1][0]).days != 1 for i in range(1, len(rows))):
        raise ValueError("领用日历有重复或缺失日期，请补全零需求日")
    values = [item[1] for item in rows]
    fitted = []
    baseline = []
    for index in range(len(values) - 28, len(values)):
        fitted.append(_rate(values[:index], alpha, variant == "sba"))
        baseline.append(statistics.mean(values[:index]))
    actual = values[-28:]
    mae = statistics.mean(abs(a - b) for a, b in zip(actual, fitted))
    baseline_mae = statistics.mean(abs(a - b) for a, b in zip(actual, baseline))
    rate = _rate(values, alpha, variant == "sba")
    nonzero_count = sum(value > 0 for value in values)
    if rows[-1][0].toordinal() + horizon > date.max.toordinal():
        raise ValueError("预测末日超出日历支持范围，请检查最后观测日期")
    future = [{"day": str(rows[-1][0] + timedelta(days=i)), "expected": round(rate, 4), "cumulative": round(rate * i, 3)} for i in range(1, horizon + 1)]
    warnings = []
    if nonzero_count < 5:
        warnings.append("非零领用日少于 5 个，预测对单次领用非常敏感。")
    if mae > baseline_mae:
        warnings.append("本次末 28 天回测 MAE 高于历史均值基线，应保留人工复核。")
    return {
        "summary": f"{part} 在 {len(rows)} 天中有 {nonzero_count} 个领用日；{variant.upper()} 预计未来 {horizon} 天领用 {rate * horizon:.2f} 件。",
        "metrics": [{"label": "平滑领用率", "value": round(rate, 4), "unit": "件/天"}, {"label": "预测期累计", "value": round(rate * horizon, 2), "unit": "件"}, {"label": "末28天 MAE", "value": round(mae, 4), "unit": "件"}, {"label": "非零日占比", "value": round(nonzero_count / len(values) * 100, 2), "unit": "%"}],
        "table": {"columns": [{"key": "day", "label": "预测日期"}, {"key": "expected", "label": "当日领用期望"}, {"key": "cumulative", "label": "累计领用期望"}], "rows": future},
        "chart": {"type": "line", "labels": [str(row[0]) for row in rows[-28:]], "series": [{"name": "真实领用", "values": actual}, {"name": "滚动一步预测", "values": [round(value, 4) for value in fitted]}]},
        "findings": [f"历史均值基线 MAE={baseline_mae:.4f}；本次模型 MAE={mae:.4f}，回测每个预测仅使用前一天及更早数据。", f"α={alpha:g}；最后观测日 {rows[-1][0]}；未来 {horizon} 天按同一稳定领用率外推。", "领用期望可以是小数；需要整数备料时应在例会结合检修计划另行决策。"],
        "method": metadata()["method"], "warnings": warnings,
    }
