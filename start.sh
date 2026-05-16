#!/bin/bash
# ============================================
# Polymarket 量化交易系统 - 启动脚本
# ============================================

set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Polymarket 量化交易系统${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""

# 检查Python版本
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}错误: Python3 未安装${NC}"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "Python版本: ${GREEN}${PY_VERSION}${NC}"

# 检查.env文件
if [ ! -f .env ]; then
    echo -e "${YELLOW}警告: .env 文件不存在${NC}"
    echo -e "正在从 .env.example 复制..."
    cp .env.example .env
    echo -e "${RED}请编辑 .env 文件填入你的钱包信息后再运行!${NC}"
    echo -e "运行: nano .env 或 vim .env"
    exit 1
fi

# 检查依赖
echo -e "检查依赖..."
if ! python3 -c "import py_clob_client" 2>/dev/null; then
    echo -e "${YELLOW}安装依赖中...${NC}"
    pip install -r requirements.txt
fi

# 检查DRY_RUN模式
DRY_RUN=$(python3 -c "
from dotenv import load_dotenv
import os
load_dotenv()
print(os.getenv('DRY_RUN', 'true'))
")

if [ "$DRY_RUN" = "true" ]; then
    echo -e "${YELLOW}当前模式: 模拟 (DRY_RUN=true)${NC}"
    echo -e "${YELLOW}不会花真钱，所有交易都是模拟的${NC}"
else
    echo -e "${RED}当前模式: 实盘 (DRY_RUN=false)${NC}"
    echo -e "${RED}将会花真钱交易!${NC}"
    echo -n "确认要启动实盘模式吗? (输入 YES 确认): "
    read -r CONFIRM
    if [ "$CONFIRM" != "YES" ]; then
        echo "已取消"
        exit 0
    fi
fi

echo ""
echo -e "${GREEN}启动交易系统...${NC}"
echo ""

python3 bot.py
