#!/bin/bash
# run_local_tests.sh — 本地运行打印流生成+虚拟打印机验证全流程
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
# 兼容两种位置: 工程内 tools/ (开源仓库) 或独立 tools/ 目录
if [ -d "$DIR/../app/src/main/cpp" ]; then
    ENCODER_DIR="$DIR/../app/src/main/cpp"
else
    ENCODER_DIR="$DIR/../OTGPrintApp/app/src/main/cpp"
fi
OUT="$DIR/test_output"
mkdir -p "$OUT"

echo "============================================================"
echo " OTGPrint 本地测试: 打印流生成 → 虚拟打印机验证"
echo "============================================================"

# Step 1: 编译测试生成器
echo ""
echo "[1/3] 编译测试生成器..."
clang++ -std=c++17 -Wall -Wextra -I"$ENCODER_DIR" \
    "$DIR/gen_test_streams.cpp" \
    "$ENCODER_DIR/pcl3gui_encoder.cpp" \
    -o "$OUT/gen_test_streams"

# Step 2: 生成测试打印流
echo ""
echo "[2/3] 生成测试打印流..."
"$OUT/gen_test_streams" "$OUT"

# Step 3: 虚拟打印机验证
echo ""
echo "[3/3] 虚拟打印机验证..."
python3 "$DIR/virtual_printer.py" "$OUT"

echo ""
echo "============================================================"
echo " 全部测试完成! 解码预览图在 $OUT/*_decoded.ppm"
echo "============================================================"
