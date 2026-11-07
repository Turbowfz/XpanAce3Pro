#!/system/bin/sh
# X-Pan 模块卸载清理：把 hook APK 一起卸掉，做到"卸载即还原"。
#
# 踩过的坑（都实锤过）：
#  1) 本脚本可能在开机早期被执行，那时包服务没起来，直接 pm uninstall 静默失败；
#  2) 在这里起后台任务也不行 —— 脚本一退出后台进程就被带走；
#  3) 用 `pm list packages | grep` 判断包在不在，开机早期会误判成"已卸载"；
#  4) 只在 service.d 放延迟脚本 = 用户不重启就永远不执行，表现为"卸载了 APK 还在"；
#  5) 旧版把 umount/清理写在 `exit 0` 之后，那几行从来没执行过。
#
# 现在做双保险：
#  A. 先立即尝试卸一次（用户在系统正常运行时卸载模块 → 这一步就成）；
#  B. 再往 /data/adb/service.d 放延迟脚本兜底（开机早期卸载的情况，下次开机补卸）。

LOG=/data/local/tmp/xpan_uninstall.log
: >> "$LOG"
log() { echo "$(date '+%m-%d %H:%M:%S') uninstall.sh: $1" >> "$LOG"; }

PKG=com.xpanport.hook

log "begin (uid=$(id -u))"

# ---- A. 立即尝试（系统在跑时 pm 可用）----
try_uninstall_now() {
    [ "$(id -u)" = "0" ] || return 1
    am force-stop com.oplus.camera >/dev/null 2>&1
    pm path "$PKG" >/dev/null 2>&1 || { log "immediate: not installed"; return 0; }
    OUT="$(pm uninstall --user 0 "$PKG" 2>&1)"
    log "immediate --user 0: $OUT"
    case "$OUT" in *uccess*) return 0 ;; esac
    OUT="$(pm uninstall "$PKG" 2>&1)"
    log "immediate plain: $OUT"
    case "$OUT" in *uccess*) return 0 ;; esac
    return 1
}
if try_uninstall_now; then
    log "immediate uninstall OK"
else
    log "immediate uninstall unavailable/failed, relying on boot cleanup"
fi

# ---- B. 延迟兜底（开机后补卸；已卸成功也留着做二次校验，幂等无害）----
SD=/data/adb/service.d
mkdir -p "$SD" 2>/dev/null
CLEAN="$SD/xpan_cleanup.sh"

cat > "$CLEAN" <<'EOS'
#!/system/bin/sh
# X-Pan 模块卸载兜底清理：等系统起完，把 hook APK 卸掉，然后自删。幂等。
LOG=/data/local/tmp/xpan_uninstall.log
PKG=com.xpanport.hook

log() { echo "$(date '+%m-%d %H:%M:%S') cleanup: $1" >> "$LOG"; }
log "start (pid $$)"

# 等包服务就绪：boot_completed + pm 能应答，最多等 10 分钟
i=0
while [ "$i" -lt 120 ]; do
    i=$((i+1))
    if [ "$(getprop sys.boot_completed 2>/dev/null)" = "1" ] \
        && pm path android >/dev/null 2>&1; then
        break
    fi
    sleep 5
done
log "package service ready after $((i*5))s"
sleep 20

# 相机必须停掉：它进程里还挂着模块，卸载可能被拒
am force-stop com.oplus.camera >/dev/null 2>&1
sleep 2

n=0
while [ "$n" -lt 20 ]; do
    n=$((n+1))
    if ! pm path "$PKG" >/dev/null 2>&1; then
        log "not installed (try $n) - nothing to do"
        break
    fi
    OUT="$(pm uninstall --user 0 "$PKG" 2>&1)"
    log "try $n (--user 0): $OUT"
    case "$OUT" in *uccess*) break ;; esac
    OUT="$(pm uninstall "$PKG" 2>&1)"
    log "try $n (plain): $OUT"
    case "$OUT" in *uccess*) break ;; esac
    sleep 8
done

if pm path "$PKG" >/dev/null 2>&1; then
    log "GAVE UP after $n tries - uninstall manually from LSPosed/KernelSU"
else
    log "PKG removed"
fi

rm -f /data/adb/service.d/xpan_cleanup.sh
log "done, self removed"
EOS

chmod 0755 "$CLEAN" 2>/dev/null
log "boot cleanup placed at $CLEAN"

# ---- 运行期痕迹（bind 挂载重启即消失，这里主动摘一次）----
umount /odm/etc/camera/config/oplus_camera_config >/dev/null 2>&1
umount /odm/etc/camera/config/camera_unit_config >/dev/null 2>&1
umount /odm/etc/camera/config/camera_unit_feature_config.protobuf >/dev/null 2>&1
rm -f /data/local/tmp/oc_active.enc
rm -f /sdcard/Android/data/com.oplus.camera/files/xpan_cropzoom
rm -f /sdcard/Android/data/com.oplus.camera/files/xpan_cropzoom_busy
log "runtime traces cleaned"

log "end"
exit 0
