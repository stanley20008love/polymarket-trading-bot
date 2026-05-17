"""
Polymarket V6.0 - GFS/Open-Meteo 天气数据源
来源: alteregoeth-ai/weatherbot, ColdMath ($300→$219K)
逻辑: 使用集合预报数据计算真实概率，与市场隐含概率比较寻找定价错误

数据源:
1. Open-Meteo API (免费) — 提供 GFS 13km 集合预报的 31 个成员
2. Polymarket 天气市场 — 从 Gamma API 获取温度合约

核心算法:
- 从 GFS 集合预报计算 P(温度 > 阈值) = 超过阈值的成员数 / 总成员数
- 与市场隐含概率比较，偏差 > 5% 即为定价错误
- 使用 Brier Score 持续校准预报准确性
"""
import json
import logging
import time
import requests
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("polymarket")


@dataclass
class WeatherForecast:
    """GFS 集合预报结果"""
    city: str
    variable: str  # temperature_max, temperature_min, precipitation
    date: str
    ensemble_mean: float
    ensemble_std: float
    ensemble_members: list = field(default_factory=list)
    forecast_prob: float = 0.0  # P(事件发生) 基于集合预报
    source: str = "open-meteo-gfs"
    fetch_time: float = 0.0


@dataclass
class WeatherMarket:
    """天气市场合约"""
    market_id: str
    question: str
    city: str
    variable: str
    threshold: float
    comparison: str  # above, below, between
    date: str
    market_prob: float  # 市场隐含概率
    yes_price: float
    no_price: float
    volume: float
    liquidity: float


# Polymarket 活跃天气城市 → Open-Meteo 坐标映射
CITY_COORDS = {
    "new-york": {"lat": 40.7128, "lon": -74.0060, "name": "New York"},
    "los-angeles": {"lat": 34.0522, "lon": -118.2437, "name": "Los Angeles"},
    "chicago": {"lat": 41.8781, "lon": -87.6298, "name": "Chicago"},
    "miami": {"lat": 25.7617, "lon": -80.1918, "name": "Miami"},
    "dallas": {"lat": 32.7767, "lon": -96.7970, "name": "Dallas"},
    "denver": {"lat": 39.7392, "lon": -104.9903, "name": "Denver"},
    "atlanta": {"lat": 33.7490, "lon": -84.3880, "name": "Atlanta"},
    "seattle": {"lat": 47.6062, "lon": -122.3321, "name": "Seattle"},
    "boston": {"lat": 42.3601, "lon": -71.0589, "name": "Boston"},
    "phoenix": {"lat": 33.4484, "lon": -112.0740, "name": "Phoenix"},
    "san-francisco": {"lat": 37.7749, "lon": -122.4194, "name": "San Francisco"},
    "houston": {"lat": 29.7604, "lon": -95.3698, "name": "Houston"},
    "washington-dc": {"lat": 38.9072, "lon": -77.0369, "name": "Washington DC"},
    "philadelphia": {"lat": 39.9526, "lon": -75.1652, "name": "Philadelphia"},
    "london": {"lat": 51.5074, "lon": -0.1278, "name": "London"},
    "tokyo": {"lat": 35.6762, "lon": 139.6503, "name": "Tokyo"},
    "buenos-aires": {"lat": -34.6037, "lon": -58.3816, "name": "Buenos Aires"},
    "cape-town": {"lat": -33.9249, "lon": 18.4241, "name": "Cape Town"},
    "sydney": {"lat": -33.8688, "lon": 151.2093, "name": "Sydney"},
    "mumbai": {"lat": 19.0760, "lon": 72.8777, "name": "Mumbai"},
}


class WeatherDataFetcher:
    """
    GFS 集合预报数据获取器
    使用 Open-Meteo 免费 API 获取 31 个集合成员预报
    
    API 文档: https://open-meteo.com/en/docs/ensemble-api
    """
    
    def __init__(self, config=None):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._cache = {}  # city -> (timestamp, WeatherForecast)
        self.cache_ttl = 3600  # 1小时缓存
        self.last_fetch_time = 0
        self.total_fetches = 0
        self.failed_fetches = 0
        
    def fetch_ensemble_forecast(self, city: str) -> Optional[WeatherForecast]:
        """
        获取 GFS 集合预报 (31个成员)
        返回每个成员的最高温度预测
        
        Open-Meteo Ensemble API 参数:
        - models: gfs_seamless (GFS 13km)
        - members: 31 (GFS 集合预报成员数)
        - temperature_2m_max: 日最高温度
        """
        now = time.time()
        
        # 检查缓存
        if city in self._cache:
            ts, forecast = self._cache[city]
            if now - ts < self.cache_ttl:
                return forecast
        
        coords = CITY_COORDS.get(city)
        if not coords:
            logger.debug(f"未知城市: {city}")
            return None
        
        try:
            url = "https://ensemble-api.open-meteo.com/v1/ensemble"
            params = {
                "latitude": coords["lat"],
                "longitude": coords["lon"],
                "models": "gfs_seamless",
                "daily": "temperature_2m_max",
                "timeformat": "unixtime",
                "timezone": "auto",
                "forecast_days": 7,
            }
            
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            
            self.total_fetches += 1
            self.last_fetch_time = now
            
            # 解析集合预报
            daily = data.get("daily", {})
            time_list = daily.get("time", [])
            temp_data = daily.get("temperature_2m_max", {})
            
            if not time_list or not temp_data:
                logger.warning(f"Open-Meteo 返回空数据: {city}")
                self.failed_fetches += 1
                return None
            
            # 集合成员数据格式: {"member00": [t1, t2, ...], "member01": [...], ...}
            # 或者单一值格式: [t1, t2, ...]
            members = []
            forecasts = []
            
            for i, ts in enumerate(time_list[:7]):  # 未来7天
                date_str = time.strftime("%Y-%m-%d", time.gmtime(ts))
                day_members = []
                
                # 尝试获取集合成员
                if isinstance(temp_data, dict):
                    for key in sorted(temp_data.keys()):
                        if key.startswith("member") and i < len(temp_data[key]):
                            val = temp_data[key][i]
                            if val is not None:
                                day_members.append(float(val))
                elif isinstance(temp_data, list) and i < len(temp_data):
                    # 单一预报，无集合成员
                    val = temp_data[i]
                    if val is not None:
                        day_members.append(float(val))
                
                if day_members:
                    mean_temp = sum(day_members) / len(day_members)
                    std_temp = (sum((t - mean_temp)**2 for t in day_members) / len(day_members)) ** 0.5 if len(day_members) > 1 else 2.0
                    
                    forecast = WeatherForecast(
                        city=city,
                        variable="temperature_max",
                        date=date_str,
                        ensemble_mean=round(mean_temp, 1),
                        ensemble_std=round(std_temp, 1),
                        ensemble_members=day_members,
                        source="open-meteo-gfs",
                        fetch_time=now,
                    )
                    forecasts.append(forecast)
                    members = day_members  # 保存最后一天的成员
            
            if not forecasts:
                self.failed_fetches += 1
                return None
            
            # 缓存最新的预报（使用最近的一天）
            latest = forecasts[0]
            self._cache[city] = (now, latest)
            
            logger.info(f"天气数据: {city} → {latest.date} 均温={latest.ensemble_mean}°C ±{latest.ensemble_std}°C ({len(latest.ensemble_members)} 成员)")
            return latest
            
        except Exception as e:
            self.failed_fetches += 1
            logger.warning(f"Open-Meteo 请求失败 ({city}): {e}")
            return None
    
    def calc_prob_above(self, city: str, threshold_celsius: float, date_offset: int = 0) -> float:
        """
        计算 P(温度 > 阈值) 基于 GFS 集合预报
        
        这是天气策略的核心算法:
        1. 获取 31 个集合成员的预报温度
        2. 统计超过阈值的成员比例
        3. 这个比例就是模型预测的真实概率
        
        例: 31个成员中有22个预测温度>30°C → P=22/31=0.71
        如果市场YES价格=0.55 → 市场低估了 → 买入YES
        """
        forecast = self.fetch_ensemble_forecast(city)
        if not forecast or not forecast.ensemble_members:
            return 0.5  # 无数据时返回均匀先验
        
        members = forecast.ensemble_members
        above_count = sum(1 for t in members if t > threshold_celsius)
        prob = above_count / len(members)
        
        logger.debug(f"P(temp>{threshold_celsius}°C | {city}) = {prob:.3f} ({above_count}/{len(members)} members)")
        return prob
    
    def calc_prob_below(self, city: str, threshold_celsius: float, date_offset: int = 0) -> float:
        """计算 P(温度 < 阈值)"""
        return 1.0 - self.calc_prob_above(city, threshold_celsius, date_offset)
    
    def find_mispricing(self, market: WeatherMarket) -> Optional[dict]:
        """
        检测天气市场定价错误
        
        返回:
        - 如果 |模型概率 - 市场概率| > 5%, 返回交易信号
        - 否则返回 None
        """
        # 获取模型概率
        if market.comparison == "above":
            model_prob = self.calc_prob_above(market.city, market.threshold)
        elif market.comparison == "below":
            model_prob = self.calc_prob_below(market.city, market.threshold)
        else:
            return None
        
        # 计算偏差
        deviation = model_prob - market.market_prob
        
        # 偏差太小不值得交易 (扣手续费后可能无利)
        if abs(deviation) < 0.05:
            return None
        
        # 确定方向
        if deviation > 0:
            # 模型认为概率更高 → 市场低估 → 买入YES
            side = "YES"
            edge = deviation
        else:
            # 模型认为概率更低 → 市场高估 → 买入NO
            side = "NO"
            edge = abs(deviation)
        
        # 手续费侵蚀检查 (天气市场5%手续费)
        fee_rate = 0.05
        price = market.yes_price if side == "YES" else market.no_price
        fee_impact = fee_rate * price * (1 - price)
        net_edge = edge - fee_impact
        
        if net_edge <= 0:
            return None  # 手续费吃掉了全部edge
        
        # 预报准确性加成 (集合成员越多越可靠)
        forecast = self.fetch_ensemble_forecast(market.city)
        member_count = len(forecast.ensemble_members) if forecast and forecast.ensemble_members else 1
        forecast_confidence = min(1.0, member_count / 15)  # 15+成员算高置信
        
        # 流动性检查
        if market.liquidity < 5000:
            return None  # 流动性不足
        
        return {
            "type": "WEATHER_GFS_MISPRICED",
            "market_id": market.market_id,
            "city": market.city,
            "side": side,
            "price": price,
            "model_prob": round(model_prob, 4),
            "market_prob": round(market.market_prob, 4),
            "deviation": round(deviation, 4),
            "edge": round(edge, 4),
            "net_edge": round(net_edge, 4),
            "fee_impact": round(fee_impact, 4),
            "forecast_confidence": round(forecast_confidence, 3),
            "member_count": member_count,
            "threshold": market.threshold,
            "comparison": market.comparison,
            "confidence": "HIGH" if edge > 0.10 and forecast_confidence > 0.7 else "MEDIUM",
            "reason": f"GFS集合{member_count}成员 P({market.comparison} {market.threshold}°C)={model_prob:.1%} vs 市场={market.market_prob:.1%} edge={edge:.1%} net={net_edge:.1%}",
        }
    
    def parse_weather_market(self, market_data: dict) -> Optional[WeatherMarket]:
        """
        从 Polymarket 市场数据解析天气合约信息
        
        天气市场问题格式:
        - "Will the temperature in New York exceed 90°F on June 15?"
        - "Will it be over 30°C in London on July 1st?"
        - "Will the high temperature in Chicago be above 95°F on June 20?"
        """
        question = (market_data.get("question", "") or "").lower()
        
        # 识别城市
        city = None
        for city_key, coords in CITY_COORDS.items():
            city_name = coords["name"].lower()
            if city_key.replace("-", " ") in question or city_name in question:
                city = city_key
                break
        
        if not city:
            return None
        
        # 识别温度阈值 (支持°F和°C)
        threshold = None
        comparison = None
        variable = "temperature_max"
        
        # 华氏度
        import re
        f_match = re.search(r'(?:exceed|above|over|higher than|more than)\s*(\d+)\s*°?\s*f', question)
        if f_match:
            threshold_f = float(f_match.group(1))
            threshold = (threshold_f - 32) * 5 / 9  # 转换为摄氏度
            comparison = "above"
        
        if not threshold:
            c_match = re.search(r'(?:exceed|above|over|higher than|more than)\s*(\d+)\s*°?\s*c', question)
            if c_match:
                threshold = float(c_match.group(1))
                comparison = "above"
        
        if not threshold:
            below_f = re.search(r'(?:below|under|less than|lower than)\s*(\d+)\s*°?\s*f', question)
            if below_f:
                threshold_f = float(below_f.group(1))
                threshold = (threshold_f - 32) * 5 / 9
                comparison = "below"
        
        if not threshold:
            below_c = re.search(r'(?:below|under|less than|lower than)\s*(\d+)\s*°?\s*c', question)
            if below_c:
                threshold = float(below_c.group(1))
                comparison = "below"
        
        if not threshold or not comparison:
            return None
        
        # 解析价格
        prices = market_data.get("outcomePrices", "[]")
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except:
                prices = []
        yes_price = float(prices[0]) if len(prices) > 0 else 0.0
        no_price = float(prices[1]) if len(prices) > 1 else 0.0
        
        return WeatherMarket(
            market_id=market_data.get("id", ""),
            question=market_data.get("question", ""),
            city=city,
            variable=variable,
            threshold=round(threshold, 1),
            comparison=comparison,
            date="",  # 从问题中提取日期（可选）
            market_prob=yes_price,
            yes_price=yes_price,
            no_price=no_price,
            volume=float(market_data.get("volumeNum", 0) or 0),
            liquidity=float(market_data.get("liquidityNum", 0) or 0),
        )
    
    def get_stats(self) -> dict:
        """返回天气数据获取器统计"""
        return {
            "cities_supported": len(CITY_COORDS),
            "total_fetches": self.total_fetches,
            "failed_fetches": self.failed_fetches,
            "cache_size": len(self._cache),
            "last_fetch": time.strftime("%H:%M:%S", time.gmtime(self.last_fetch_time)) if self.last_fetch_time else "never",
            "success_rate": f"{(1 - self.failed_fetches/max(1,self.total_fetches))*100:.0f}%",
        }
