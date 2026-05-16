"""
Polymarket 量化交易系统 - 快速测试脚本
不需要钱包私钥即可运行，验证系统功能
"""
import logging
import sys
import os

# 添加当前目录到path
sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from market_scanner import MarketScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("test")


def test_api_connection():
    """测试API连接"""
    print("\n" + "=" * 50)
    print("  测试1: API连接")
    print("=" * 50)

    config = Config()
    scanner = MarketScanner(config)

    markets = scanner.fetch_active_markets(limit=20)
    if markets:
        print(f"✅ 成功获取 {len(markets)} 个活跃市场")
        print("\n  热门市场:")
        for m in markets[:5]:
            print(f"    {m.question[:50]}")
            print(f"      YES={m.yes_price:.3f} NO={m.no_price:.3f} 总价={m.total_price:.3f}")
            print(f"      成交量=${m.volume:,.0f} 流动性=${m.liquidity:,.0f}")
            print(f"      手续费: Maker={m.maker_fee*100:.1f}% Taker={m.taker_fee*100:.1f}%")
    else:
        print("❌ 获取市场数据失败")
        return False

    return True


def test_scanner_strategies():
    """测试策略扫描"""
    print("\n" + "=" * 50)
    print("  测试2: 策略扫描")
    print("=" * 50)

    config = Config()
    scanner = MarketScanner(config)

    results = scanner.scan_all()
    print(f"  总市场数: {results['total_markets']}")

    # 套利
    arbs = results["arbitrage"]
    print(f"\n  套利机会: {len(arbs)}个")
    for a in arbs[:3]:
        m = a["market"]
        print(f"    {m.question[:40]}")
        print(f"      YES={a['yes_price']:.3f} NO={a['no_price']:.3f} 空间={a['arb_spread']*100:.2f}%")

    # 均值回归
    mean_rev = results["mean_reversion"]
    print(f"\n  均值回归机会: {len(mean_rev)}个")
    for a in mean_rev[:3]:
        m = a["market"]
        print(f"    {m.question[:40]}")
        print(f"      {a['side']} @ {a['price']:.3f} 潜在回报={a['potential_return']:.1f}x")
        print(f"      原因: {a['reason']}")

    # 事件驱动
    events = results["event_driven"]
    print(f"\n  事件驱动机会: {len(events)}个")
    for a in events[:3]:
        m = a["market"]
        print(f"    {m.question[:40]}")
        print(f"      {a['side']} @ {a['price']:.3f}")
        print(f"      原因: {a['reason']}")

    return True


def test_risk_manager():
    """测试风控模块"""
    print("\n" + "=" * 50)
    print("  测试3: 风控模块")
    print("=" * 50)

    config = Config()
    from risk_manager import RiskManager

    rm = RiskManager(config)

    # 测试仓位计算
    for cap in [100, 500, 1000]:
        size = rm.calculate_position_size(cap)
        print(f"  资金${cap} → 仓位${size:.2f} ({config.TRADE_SIZE_PERCENT}%)")

    # 测试交易检查
    can, reason = rm.check_can_trade(10)
    print(f"  交易检查 $10: {'✅' if can else '❌'} {reason}")

    can, reason = rm.check_can_trade(200)
    print(f"  交易检查 $200: {'✅' if can else '❌'} {reason}")

    # 测试状态
    status = rm.get_status()
    print(f"  风控状态: {status}")

    return True


def test_clob_client():
    """测试CLOB客户端"""
    print("\n" + "=" * 50)
    print("  测试4: CLOB客户端 (只读)")
    print("=" * 50)

    try:
        from py_clob_client.client import ClobClient

        client = ClobClient("https://clob.polymarket.com")
        ok = client.get_ok()
        server_time = client.get_server_time()
        print(f"  CLOB状态: {'✅ 在线' if ok else '❌ 离线'}")
        print(f"  服务器时间: {server_time}")

        # 获取简化市场
        markets = client.get_simplified_markets()
        if markets and "data" in markets:
            print(f"  市场数量: {len(markets['data'])}")
            # 获取第一个市场的中间价
            if markets["data"]:
                m = markets["data"][0]
                tokens = m.get("tokens", [])
                if tokens:
                    token_id = tokens[0].get("token_id", "")
                    if token_id:
                        try:
                            mid = client.get_midpoint(token_id)
                            print(f"  示例中间价: {mid}")
                        except Exception as e:
                            print(f"  中间价获取: {e}")

        return True

    except ImportError:
        print("  ⚠️ py-clob-client 未安装")
        print("  运行: pip install py-clob-client")
        return False
    except Exception as e:
        print(f"  ❌ CLOB客户端错误: {e}")
        return False


def main():
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║  Polymarket 量化交易系统 - 快速测试          ║")
    print("╚══════════════════════════════════════════════╝")

    results = []
    results.append(("API连接", test_api_connection()))
    results.append(("策略扫描", test_scanner_strategies()))
    results.append(("风控模块", test_risk_manager()))
    results.append(("CLOB客户端", test_clob_client()))

    print("\n" + "=" * 50)
    print("  测试结果汇总")
    print("=" * 50)
    for name, result in results:
        emoji = "✅" if result else "❌"
        print(f"  {emoji} {name}")

    all_pass = all(r for _, r in results)
    if all_pass:
        print("\n🎉 所有测试通过! 可以运行 python3 bot.py 开始交易")
    else:
        print("\n⚠️ 部分测试失败，请检查上方错误信息")

    print()


if __name__ == "__main__":
    main()
