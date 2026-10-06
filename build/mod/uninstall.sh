#!/system/bin/sh
# 卸载模块时的清理：把尺寸修正 hook 的 APK 一起卸掉，做到"卸载即还原"。
#
# 踩过的坑：
#  1) 本脚本在开机早期就被执行，那时包服务还没起来，直接 pm uninstall 会静默失败；
#  2) 在这里起后台任务也不行 —— 脚本一退出后台进程就被带走；
#  3) 用 `pm list packages | grep` 判断包在不在，在开机早期会误判成"已卸载"。
# 所以：改成往 KernelSU 的 /data/adb/service.d 放一个延迟脚本（那是系统基本起来后才跑），
# 那里 sleep 一会儿再用 `pm path` 判断、`pm uninstall` 卸载，跑完自删。

SD=/data/adb/service.d
mkdir -p "$SD" 2>/dev/null
CLEAN="$SD/xpan_cleanup.sh"

cat > "$CLEAN" <<'EOS'
#!/system/bin/sh
LOG=/data/local/tmp/xpan_uninstall.log
echo "cleanup start $(date)" >> $LOG

# 等系统起来，包服务可用
sleep 45

i=0
while [ $i -lt 40 ]; do
    i=$((i+1))
    if ! pm path com.xpanport.hook >/dev/null 2>&1; then
        echo "hook apk not present (try $i)" >> $LOG
        break
    fi
    OUT="$(pm uninstall com.xpanport.hook 2>&1)"
    echo "try $i: $OUT" >> $LOG
    case "$OUT" in
        *uccess*) break ;;
    esac
    sleep 5
done

rm -f /data/adb/service.d/xpan_cleanup.sh
echo "cleanup done $(date)" >> $LOG
EOS

chmod 755 "$CLEAN" 2>/dev/null
echo "uninstall.sh: 已放置延迟清理脚本" >> /data/local/tmp/xpan_uninstall.log
exit 0
