#!/bin/bash
# Job Hunter 每日运行脚本
# 由 launchd 定时调用，或手动运行: bash run_daily.sh
#
# 前置条件:
#   1. Chrome 已启动并监听 9222 端口
#   2. Chrome 中已登录 Boss直聘
#   3. Python 3 + DrissionPage 已安装

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

# 日志目录
LOG_DIR="$PROJECT_DIR/data/logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RUN_LOG="$LOG_DIR/run_${TIMESTAMP}.log"

echo "=== Job Hunter 开始 $(date '+%Y-%m-%d %H:%M:%S') ===" | tee "$RUN_LOG"

# 0. 随机延迟（5-15 分钟），避免精确整点触发
JITTER=$(( RANDOM % 600 + 300 ))  # 300-900 秒
echo "⏳ 随机延迟 ${JITTER}s（模拟人类）..." | tee -a "$RUN_LOG"
sleep "$JITTER"

# 1. 检查 Chrome 调试端口
echo "🔍 检查 Chrome 调试端口 9222..." | tee -a "$RUN_LOG"
if ! curl -s http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
    echo "❌ Chrome 调试端口 9222 未就绪！" | tee -a "$RUN_LOG"
    echo "   请先启动 Chrome:" | tee -a "$RUN_LOG"
    echo '   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \' | tee -a "$RUN_LOG"
    echo '     --remote-debugging-port=9222 \' | tee -a "$RUN_LOG"
    echo '     --user-data-dir=/tmp/chrome-debug' | tee -a "$RUN_LOG"
    exit 1
fi
echo "✅ Chrome 已就绪" | tee -a "$RUN_LOG"

# 2. 运行 Boss 直聘投递
echo "🚀 开始投递..." | tee -a "$RUN_LOG"
PYTHONUTF8=1 python3 "$PROJECT_DIR/boss_apply.py" --daily 2>&1 | tee -a "$RUN_LOG"

# 3. 生成报告
echo "📊 生成报告..." | tee -a "$RUN_LOG"
PYTHONUTF8=1 python3 "$PROJECT_DIR/report.py" 2>&1 | tee -a "$RUN_LOG"

echo "=== Job Hunter 结束 $(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "$RUN_LOG"
