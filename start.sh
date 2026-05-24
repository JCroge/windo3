#!/bin/bash

echo "启动多Agent趋势交易系统..."

# 检查依赖
if ! python3 -c "import ccxt" 2>/dev/null; then
    echo "依赖未安装，正在安装..."
    pip3 install -r requirements.txt -q
fi

# 检查配置
if [ ! -f .env ]; then
    echo "请先配置 .env 文件（参考 .env.example）"
    exit 1
fi

# 运行多Agent系统
python3 run_agents.py
