#!/system/bin/sh
# Runs after the boot-completed broadcast — on a file-based-encrypted device that is after the user
# has unlocked, which is the first moment /data/user/0/<pkg> exists.
#
# service.sh is started by KernelSU at late_start, long before that, so its `wait_for "$APP"` fails
# and it exits without staging anything. 482 tried to rescue that with a detached `--wait` copy of
# itself; the 482 report still showed grSc=-1 and "pop_port is absent", so that copy did not survive
# either. This stage does not need to guess: by the time it runs the unlock has already happened.
#
# The camera-side data used to not depend on this: post-fs-data.sh stages it into the merged
# basictone tree. Since 484 it does — that tree is a module mount and KernelSU can unmount it for the
# camera process alone (the 483 report), so service.sh copies the same four directories into the
# camera's own files here, where no mount setting reaches them. This stage is now the one that makes
# POP and GR work on a device with 卸载模块 / a Zygisk Next denylist.
#
# KernelSU only runs this if the installed version supports the boot-completed stage. If it does not,
# nothing breaks: service.sh's detached --wait copy is still there as a fallback.
MODDIR=${0%/*}

for OFF in /data/adb/omnicam_off /data/adb/omnicam_nostack_off /data/adb/omnicam_fullport_off; do
    [ -f "$OFF" ] && exit 0
done

exec sh "$MODDIR/service.sh"
