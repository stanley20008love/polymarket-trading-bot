"""
Polymarket V3 - 概率校准引擎
核心功能:
- Brier Score: 预测精度度量 (0=完美, 0.25=随机, 1=最差)
- ECE (Expected Calibration Error): 校准误差
- 校准曲线: 将预测分箱计算实际频率
- Brier Skill Score: 相对基准的改善
- 校准反馈: BSS>0.2增仓, ECE>0.1降置信度

数据流: 下单 → 记录 → Calibration → 反馈 → 调整Kelly/置信度
"""
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("polymarket")


@dataclass
class CalibrationObservation:
    """校准观测记录"""
    timestamp: float
    predicted_prob: float   # 策略预测概率
    actual_outcome: int     # 实际结果: 1=发生, 0=未发生
    strategy: str = ""      # 策略名称
    market_id: str = ""     # 市场ID


@dataclass
class CalibrationMetrics:
    """校准指标"""
    brier_score: float = 0.0
    ece: float = 0.0
    brier_skill_score: float = 0.0
    sample_size: int = 0
    calibration_curve: list = field(default_factory=list)
    reliability: float = 0.0  # 可靠性指标 [0, 1]
    strategy: str = ""


class ProbabilityCalibration:
    """
    概率校准引擎

    功能:
    1. 记录预测-结果对
    2. 计算Brier Score / ECE / BSS
    3. 生成校准曲线
    4. 根据校准质量调整置信度

    校准反馈规则:
    - Brier Skill Score > 0.2 → 策略可靠，可增仓
    - ECE > 0.1 → 策略校准差，降低置信度
    - Brier Score > 0.3 → 策略预测质量差，降权
    """

    MAX_OBSERVATIONS = 10000
    NUM_BINS = 10

    def __init__(self):
        self.observations: list[CalibrationObservation] = []
        self._metrics_cache: dict[str, CalibrationMetrics] = {}
        self._cache_timestamp: float = 0

    def record_observation(
        self,
        predicted_prob: float,
        actual_outcome: int,
        strategy: str = "",
        market_id: str = "",
        timestamp: float = None
    ):
        """
        记录一次校准观测

        参数:
            predicted_prob: 策略预测的概率 [0, 1]
            actual_outcome: 实际结果 (1=事件发生, 0=未发生)
            strategy: 策略名称
            market_id: 市场ID
        """
        obs = CalibrationObservation(
            timestamp=timestamp or time.time(),
            predicted_prob=max(0, min(1, predicted_prob)),
            actual_outcome=1 if actual_outcome else 0,
            strategy=strategy,
            market_id=market_id,
        )
        self.observations.append(obs)

        # 限制观测数量
        if len(self.observations) > self.MAX_OBSERVATIONS:
            self.observations = self.observations[-self.MAX_OBSERVATIONS:]

        # 清除缓存
        self._metrics_cache.pop(strategy, None)

    def compute_brier_score(self, strategy: str = "", min_samples: int = 10) -> float:
        """
        计算Brier Score
        BS = (1/N) * Σ(f_i - o_i)²
        f_i = 预测概率, o_i = 实际结果

        参数:
            strategy: 策略名(空=所有策略)
            min_samples: 最小样本量

        返回:
            Brier Score [0, 1], 0=完美, 0.25=随机, 1=最差
        """
        obs_list = self._filter_observations(strategy)
        if len(obs_list) < min_samples:
            return 0.25  # 默认随机水平

        total = sum((o.predicted_prob - o.actual_outcome) ** 2 for o in obs_list)
        return total / len(obs_list)

    def compute_ece(self, strategy: str = "", min_samples: int = 10) -> float:
        """
        计算Expected Calibration Error (ECE)
        ECE = Σ (n_b/N) * |avg_confidence_b - avg_accuracy_b|

        将预测分入NUM_BINS个等宽箱, 计算每箱的置信度与准确率之差

        参数:
            strategy: 策略名
            min_samples: 最小样本量

        返回:
            ECE [0, 1], 0=完美校准
        """
        obs_list = self._filter_observations(strategy)
        if len(obs_list) < min_samples:
            return 0.1  # 默认

        # 分箱
        bins = [[] for _ in range(self.NUM_BINS)]
        for o in obs_list:
            bin_idx = min(int(o.predicted_prob * self.NUM_BINS), self.NUM_BINS - 1)
            bins[bin_idx].append(o)

        # 计算ECE
        total_n = len(obs_list)
        ece = 0.0
        for bin_obs in bins:
            n_b = len(bin_obs)
            if n_b == 0:
                continue
            avg_confidence = sum(o.predicted_prob for o in bin_obs) / n_b
            avg_accuracy = sum(o.actual_outcome for o in bin_obs) / n_b
            ece += (n_b / total_n) * abs(avg_confidence - avg_accuracy)

        return ece

    def compute_brier_skill_score(self, strategy: str = "", min_samples: int = 10) -> float:
        """
        计算Brier Skill Score (BSS)
        BSS = 1 - BS_model / BS_reference
        BS_reference = 埀准Brier Score(使用历史平均频率作为预测)

        参数:
            strategy: 策略名
            min_samples: 最小样本量

        返回:
            BSS, >0表示优于基准, >0.2表示策略可靠
        """
        obs_list = self._filter_observations(strategy)
        if len(obs_list) < min_samples:
            return 0.0

        # 模型Brier Score
        bs_model = self.compute_brier_score(strategy, min_samples=1)

        # 基准Brier Score (使用平均频率)
        avg_freq = sum(o.actual_outcome for o in obs_list) / len(obs_list)
        bs_ref = sum((avg_freq - o.actual_outcome) ** 2 for o in obs_list) / len(obs_list)

        if bs_ref <= 0:
            return 0.0

        return 1 - bs_model / bs_ref

    def compute_calibration_curve(self, strategy: str = "") -> list[dict]:
        """
        计算校准曲线
        将预测分箱，每箱返回{predicted: 平均预测概率, actual: 实际频率, count: 样本数}
        """
        obs_list = self._filter_observations(strategy)
        if not obs_list:
            return []

        bins = [[] for _ in range(self.NUM_BINS)]
        for o in obs_list:
            bin_idx = min(int(o.predicted_prob * self.NUM_BINS), self.NUM_BINS - 1)
            bins[bin_idx].append(o)

        curve = []
        for i, bin_obs in enumerate(bins):
            if not bin_obs:
                continue
            avg_pred = sum(o.predicted_prob for o in bin_obs) / len(bin_obs)
            avg_actual = sum(o.actual_outcome for o in bin_obs) / len(bin_obs)
            curve.append({
                "predicted": round(avg_pred, 3),
                "actual": round(avg_actual, 3),
                "count": len(bin_obs),
                "bin_center": round((i + 0.5) / self.NUM_BINS, 2),
            })
        return curve

    def get_metrics(self, strategy: str = "", force_refresh: bool = False) -> CalibrationMetrics:
        """
        获取完整校准指标(带缓存)

        返回:
            CalibrationMetrics
        """
        cache_key = strategy or "_all"
        if not force_refresh and cache_key in self._metrics_cache:
            if time.time() - self._cache_timestamp < 60:  # 60秒缓存
                return self._metrics_cache[cache_key]

        obs_list = self._filter_observations(strategy)
        bs = self.compute_brier_score(strategy)
        ece = self.compute_ece(strategy)
        bss = self.compute_brier_skill_score(strategy)
        curve = self.compute_calibration_curve(strategy)

        # 可靠性: 综合指标, 越高越可靠
        reliability = max(0, min(1, (1 - bs) * (1 - ece) * (1 + bss) / 2))

        metrics = CalibrationMetrics(
            brier_score=round(bs, 4),
            ece=round(ece, 4),
            brier_skill_score=round(bss, 4),
            sample_size=len(obs_list),
            calibration_curve=curve,
            reliability=round(reliability, 4),
            strategy=strategy,
        )

        self._metrics_cache[cache_key] = metrics
        self._cache_timestamp = time.time()
        return metrics

    def get_confidence_adjustment(self, strategy: str = "") -> float:
        """
        根据校准质量返回置信度调整系数 [0, 1.5]

        规则:
        - BSS > 0.2 → 策略可靠, 系数 1.0~1.5 (增仓)
        - ECE > 0.1 → 校准差, 系数 0.5~1.0 (降置信度)
        - Brier Score > 0.3 → 预测差, 系数 0.3~0.5 (大幅降权)
        - 样本不足(<20) → 系数 0.7 (保守)
        """
        obs_list = self._filter_observations(strategy)

        if len(obs_list) < 20:
            return 0.7  # 样本不足，保守

        metrics = self.get_metrics(strategy)

        # 基础系数
        factor = 1.0

        # BSS反馈
        if metrics.brier_skill_score > 0.2:
            factor *= min(1.0 + metrics.brier_skill_score, 1.5)
            logger.info(f"[Calibration] 策略{strategy} BSS={metrics.brier_skill_score:.3f} 可靠, 增仓系数={factor:.2f}")
        elif metrics.brier_skill_score < 0:
            factor *= 0.5
            logger.warning(f"[Calibration] 策略{strategy} BSS={metrics.brier_skill_score:.3f} 差于基准, 降权")

        # ECE反馈
        if metrics.ece > 0.1:
            ece_penalty = max(0.3, 1.0 - metrics.ece)
            factor *= ece_penalty
            logger.warning(f"[Calibration] 策略{strategy} ECE={metrics.ece:.3f} 校准差, 降置信度系数={factor:.2f}")

        # Brier Score反馈
        if metrics.brier_score > 0.3:
            factor *= 0.5
            logger.warning(f"[Calibration] 策略{strategy} Brier={metrics.brier_score:.3f} 预测质量差, 大幅降权")

        return max(0.1, min(factor, 1.5))

    def _filter_observations(self, strategy: str = "") -> list[CalibrationObservation]:
        """过滤观测记录"""
        if not strategy:
            return self.observations
        return [o for o in self.observations if o.strategy == strategy]

    def get_stats(self) -> dict:
        """获取引擎统计"""
        strategies = set(o.strategy for o in self.observations if o.strategy)
        return {
            "total_observations": len(self.observations),
            "strategies_tracked": len(strategies),
            "strategy_list": list(strategies),
        }
