#!/bin/zsh
# 固件构建状态快照
PIO=~/.platformio
FW=/Users/liuxue/Desktop/www/maozi-diy/firmware
pio_size=$(du -sg $PIO 2>/dev/null | cut -f1)
pkgs=$(ls $PIO/packages 2>/dev/null | wc -l | tr -d ' ')
build_dir=$FW/.pio/build/esp32s3
if [ -f "$build_dir/firmware.bin" ]; then
    stage="✅ 编译完成 firmware.bin $(du -h $build_dir/firmware.bin | cut -f1)"
elif [ -d "$build_dir" ]; then
    objs=$(find $build_dir -name "*.o" 2>/dev/null | wc -l | tr -d ' ')
    stage="🔨 编译中：已产出 $objs 个目标文件"
else
    stage="⏳ 尚未进入编译（还在准备包）"
fi
proc=$(ps aux | grep -c "[p]latformio")
echo "包目录 ${pio_size}GB | 已装 $pkgs 个包 | $stage | platformio进程:$proc"
