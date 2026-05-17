"""
Polymarket V3 - Kelly Criterion 仓位管理模块
三大核心函数：
1. kellyBinary() — 二元市场Kelly公式
2. combinedKelly() — 多策略加权+分歧折扣
3. confidenceAdjustedKelly() — 贝叶斯收缩置信度调整

数据流: SignalGenerator → 策略 → ForecastCombiner → Kelly → RiskManager → 下单
"""
import math
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("polymarket")


@dataclass
class KellyResult:
    """Kelly计算结果"""
    fraction: float          # Kelly推荐仓位比例 [0, 1]
    raw_fraction: float      # 未经调整的原始Kelly值
    confidence: float        # 置信度 [0, 1]
    edge: float             # 期望边际 (p - price)
    adjusted: bool          # 是否经过置信度调整
    reason: str = ""        # 调整原因说明


def kellyBinary(p: float, price: float, side: str = "YES") -> KellyResult:
    """
    二元市场Kelly公式
    f* = (p * b - q) / b  其中 b = (1-price)/price (YES) 或 price/(1-price) (NO)
    简化: f* = (p - price) / (1 - price) for YES
          f* = (p - (1-price)) / price for NO (即 (p_no - no_price) / no_price类似)

    参数:
        p: 策略估算的真实概率 [0, 1]
        price: 市场当前价格 [0, 1]
        side: "YES" 或 "NO"

    返回:
        KellyResult: Kelly计算结果

    约束:
        - f* < 0 时不交易 (fraction = 0)
        - f* 上限为1 (全仓)
        - price=0或1时返回fraction=0
    """
    if price <= 0.01 or price >= 0.99:
        return KellyResult(
            fraction=0, raw_fraction=0, confidence=0,
            edge=0, adjusted=False, reason="极端价格不适用Kelly"
        )

    if p <= 0 or p >= 1:
        return KellyResult(
            fraction=0, raw_fraction=0, confidence=0,
            edge=0, adjusted=False, reason="概率超出有效范围"
        )

    if side == "YES":
        edge = p - price
        if edge <= 0:
            return KellyResult(
                fraction=0, raw_fraction=0, confidence=0,
                edge=edge, adjusted=False, reason=f"YES无边际: p={p:.3f} <= price={price:.3f}"
            )
        # f* = (p - price) / (1 - price)
        raw_f = edge / (1 - price)
    else:
        # NO side: 真实概率是1-p，市场价是1-price
        p_no = 1 - p
        price_no = 1 - price
        edge = p_no - price_no
        if edge <= 0:
            return KellyResult(
                fraction=0, raw_fraction=0, confidence=0,
                edge=edge, adjusted=False, reason=f"NO无边际: p_no={p_no:.3f} <= price_no={price_no:.3f}"
            )
        raw_f = edge / price_no

    raw_f = max(0, min(raw_f, 1.0))

    return KellyResult(
        fraction=raw_f,
        raw_fraction=raw_f,
        confidence=min(abs(edge) * 5, 1.0),  # 边际越大置信度越高
        edge=edge,
        adjusted=False,
        reason=f"原始Kelly: edge={edge:.4f}, f*={raw_f:.4f}"
    )


def combinedKelly(
    signals: list[dict],
    price: float,
    side: str = "YES",
    disagreement_discount: float = 0.5,
    min_agreement: float = 0.6
) -> KellyResult:
    """
    多策略加权Kelly + 分歧折扣
    当多个策略对同一市场给出信号时，综合考虑：
    1. 按策略历史胜率加权
    2. 如果策略间分歧大(标准差高)，应用分歧折扣

    参数:
        signals: 策略信号列表，每个包含:
            - strategy: 策略名
            - probability: 估算概率
            - weight: 策略权重(历史胜率)
            - confidence: 策略置信度
        price: 市场价格
        side: 交易方向
        disagreement_discount: 分歧折扣系数 [0, 1]
        min_agreement: 最低一致性要求

    返回:
        KellyResult: 综合Kelly结果

    算法:
        weighted_p = Σ(w_i * p_i) / Σ(w_i)
        σ = std(p_i)  // 策略间分歧
        if σ > threshold: f_combined *= (1 - disagreement_discount)
    """
    if not signals:
        return KellyResult(
            fraction=0, raw_fraction=0, confidence=0,
            edge=0, adjusted=False, reason="无信号输入"
        )

    if len(signals) == 1:
        s = signals[0]
        return kellyBinary(s.get("probability", 0.5), price, side)

    # 提取概率和权重
    probs = [s.get("probability", 0.5) for s in signals]
    weights = [s.get("weight", 0.5) for s in signals]
    confidences = [s.get("confidence", 0.5) for s in signals]

    # 加权平均概率
    total_weight = sum(weights)
    if total_weight <= 0:
        total_weight = 1
    weighted_p = sum(p * w for p, w in zip(probs, weights)) / total_weight

    # 计算策略间分歧(标准差)
    if len(probs) > 1:
        mean_p = sum(probs) / len(probs)
        variance = sum((p - mean_p) ** 2 for p in probs) / len(probs)
        std_p = math.sqrt(variance)
    else:
        std_p = 0

    # 分歧折扣: 分歧越大，折扣越深
    disagreement_factor = 1.0
    if std_p > 0.05:  # 5%以上分歧开始折扣
        disagreement_factor = max(0.1, 1.0 - disagreement_discount * min(std_p / 0.2, 1.0))
        logger.info(f"策略分歧折扣: σ={std_p:.4f}, factor={disagreement_factor:.3f}")

    # 一致性检查
    agreement = 1.0 - std_p  # 简化一致性指标
    if agreement < min_agreement:
        disagreement_factor *= (agreement / min_agreement)
        logger.warning(f"策略一致性不足: agreement={agreement:.3f} < {min_agreement}")

    # 计算Kelly
    base_kelly = kellyBinary(weighted_p, price, side)

    # 应用分歧折扣
    adjusted_fraction = base_kelly.fraction * disagreement_factor

    # 平均置信度
    avg_confidence = sum(c * w for c, w in zip(confidences, weights)) / total_weight

    return KellyResult(
        fraction=adjusted_fraction,
        raw_fraction=base_kelly.raw_fraction,
        confidence=avg_confidence * disagreement_factor,
        edge=base_kelly.edge,
        adjusted=disagreement_factor < 1.0,
        reason=f"综合Kelly: {len(signals)}策略, weighted_p={weighted_p:.3f}, "
               f"σ={std_p:.4f}, discount={disagreement_factor:.3f}"
    )


def confidenceAdjustedKelly(
    kelly_fraction: float,
    confidence: float,
    sample_size: int = 0,
    prior_strength: float = 10.0,
    kelly_cap: float = 0.25
) -> KellyResult:
    """
    贝叶斯收缩置信度调整Kelly
    核心思想: 当置信度低或样本量小时，将Kelly向0收缩(更保守)

    参数:
        kelly_fraction: 原始Kelly比例
        confidence: 信号置信度 [0, 1]
        sample_size: 历史样本量
        prior_strength: 贝叶斯先验强度(越大越保守)
        kelly_cap: Kelly上限(Quarter-Kelly=0.25推荐)

    返回:
        KellyResult: 调整后的Kelly结果

    算法:
        shrinkage = sample_size / (sample_size + prior_strength)
        adjusted_f = kelly_fraction * confidence * shrinkage
        adjusted_f = min(adjusted_f, kelly_cap)

    推荐配置:
        - Quarter-Kelly: kelly_cap = 0.25 (保守，推荐)
        - Half-Kelly: kelly_cap = 0.50 (激进)
        - Full-Kelly: kelly_cap = 1.00 (极危险，不推荐)
    """
    if kelly_fraction <= 0:
        return KellyResult(
            fraction=0, raw_fraction=kelly_fraction, confidence=confidence,
            edge=0, adjusted=True, reason="Kelly为0或负"
        )

    # 贝叶斯收缩因子
    # 当有样本时: shrinkage = n / (n + prior_strength) → 样本越多越接近1
    # 当无样本时: shrinkage = 0.5 (保守默认，不再用confidence替代以避免双重惩罚)
    if sample_size > 0:
        shrinkage = sample_size / (sample_size + prior_strength)
    else:
        shrinkage = 0.5  # 无样本时使用保守默认值，而非confidence(避免双重惩罚)

    # 调整Kelly: 只乘以置信度和收缩因子各一次
    # 修复前: adjusted_f = kelly_fraction × confidence × confidence (BUG!)
    # 修复后: adjusted_f = kelly_fraction × shrinkage (置信度已体现在kelly_fraction中)
    adjusted_f = kelly_fraction * shrinkage

    # 应用上限
    capped = adjusted_f > kelly_cap
    adjusted_f = min(adjusted_f, kelly_cap)

    # 确保非负
    adjusted_f = max(0, adjusted_f)

    reasons = []
    if shrinkage < 0.5:
        reasons.append(f"低样本收缩: shrinkage={shrinkage:.3f}")
    if confidence < 0.5:
        reasons.append(f"低置信度: confidence={confidence:.3f}")
    if capped:
        reasons.append(f"Kelly上限: {kelly_cap}")
    if not reasons:
        reasons.append("置信度调整正常")

    return KellyResult(
        fraction=adjusted_f,
        raw_fraction=kelly_fraction,
        confidence=confidence * shrinkage,
        edge=0,
        adjusted=True,
        reason="; ".join(reasons)
    )


def calculate_position_size_kelly(
    capital: float,
    kelly_fraction: float,
    price: float,
    fee_rate: float = 0.02,
    min_size: float = 5.0,
    max_pct: float = 0.15
) -> float:
    """
    根据Kelly结果计算实际下单金额

    参数:
        capital: 可用资金
        kelly_fraction: Kelly比例 (来自confidenceAdjustedKelly)
        price: 市场价格
        fee_rate: 手续费率(含滑点)
        min_size: 最小交易金额
        max_pct: 单笔最大资金占比

    返回:
        实际下单金额
    """
    if kelly_fraction <= 0 or price <= 0:
        return 0

    # Kelly仓位 = 资金 × Kelly比例
    gross_size = capital * kelly_fraction

    # 预留手续费
    net_size = gross_size / (1 + fee_rate * price * (1 - price))

    # 应用上限 (单笔不超过资金的max_pct)
    net_size = min(net_size, capital * max_pct)

    # 应用下限: 如果Kelly计算出的仓位太小(< min_size)，说明edge不足，不交易
    # 修复前: max(net_size, min_size) → 强制拉到min_size，即使Kelly认为不该交易
    # 修复后: 如果计算仓位 < min_size，返回0 (尊重Kelly判断)
    if net_size < min_size:
        return 0

    return round(net_size, 2)
