#!/bin/bash
# Job Hunter 每日运行脚本
# 由 launchd 定时调用，或手动运行: bash run_daily.sh
#
# 前置条件:
#   1. Chrome 已启动并监听调试端口（下面的 CHROME_PORT）
#   2. Chrome 中已登录 Boss直聘 / 51job
#   3. Python 3 + DrissionPage 已安装
#
# ★ 2026-09-15 修复端口不一致：本脚本原来查的是 9222，而 boss_apply.py /
#   platform_51job.py 里写死的是 9223 —— 于是这个「Chrome 就绪检查」永远失败、
#   直接 exit 1，launchd 那条路其实从来没跑起来过（最近一次 run_*.log 停在 8/26）。
#   现在端口收敛成一个变量，跟脚本实际用的端口对齐。

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

CHROME_PORT="${CHROME_PORT:-9223}"

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
echo "🔍 检查 Chrome 调试端口 $CHROME_PORT..." | tee -a "$RUN_LOG"
if ! curl -s "http://127.0.0.1:$CHROME_PORT/json/version" > /dev/null 2>&1; then
    echo "❌ Chrome 调试端口 $CHROME_PORT 未就绪！" | tee -a "$RUN_LOG"
    echo "   请先启动 Chrome:" | tee -a "$RUN_LOG"
    echo '   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \' | tee -a "$RUN_LOG"
    echo "     --remote-debugging-port=$CHROME_PORT \\" | tee -a "$RUN_LOG"
    echo '     --user-data-dir=/tmp/chrome-debug' | tee -a "$RUN_LOG"
    # 2026-09-15：这是「整轮投递全部作废」的静默失败点，必须推送告警
    PYTHONUTF8=1 python3 "$PROJECT_DIR/scripts/notify_alert.py" chrome_down \
        "Chrome 未就绪，本轮投递全部作废" \
        "launchd 触发时 $CHROME_PORT 端口连不上，今天这次投递窗口已经浪费。\n日志：$RUN_LOG" \
        error 2>/dev/null || echo "（告警未发出，见 data/logs/alerts.log）" | tee -a "$RUN_LOG"
    exit 1
fi
echo "✅ Chrome 已就绪（端口 $CHROME_PORT）" | tee -a "$RUN_LOG"

# 2. 运行 Boss 直聘投递
echo "🚀 开始投递..." | tee -a "$RUN_LOG"
PYTHONUTF8=1 python3 "$PROJECT_DIR/boss_apply.py" --daily 2>&1 | tee -a "$RUN_LOG"

# 3. 生成报告
echo "📊 生成报告..." | tee -a "$RUN_LOG"
PYTHONUTF8=1 python3 "$PROJECT_DIR/report.py" 2>&1 | tee -a "$RUN_LOG"

# 4. 同步 Boss 聊天真实状态 → 回填漏斗（已读/回复）
#    2026-09-15 新增：在此之前 read/replied 永远是 0，漏斗后半段没有数据。
#    只读：不点、不发消息；失败不影响当天投递（|| true）。
echo "💬 同步聊天状态（已读/回复）..." | tee -a "$RUN_LOG"
PYTHONUTF8=1 python3 "$PROJECT_DIR/scripts/sync_chat_status.py" --apply --port "$CHROME_PORT" \
    2>&1 | tee -a "$RUN_LOG" || true

# 5. 推送收工摘要
echo "📱 推送收工摘要..." | tee -a "$RUN_LOG"
PYTHONUTF8=1 python3 "$PROJECT_DIR/scripts/daily_summary.py" 2>&1 | tee -a "$RUN_LOG" || true

echo "=== Job Hunter 结束 $(date '+%Y-%m-%d %H:%M:%S') ===" | tee -a "$RUN_LOG"
