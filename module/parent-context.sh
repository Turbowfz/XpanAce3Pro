#!/system/bin/sh
# Observation is not approval: callers authorize, persist and recheck both pins.
xpan_parent_bounded() {
    if command -v xpan_bounded >/dev/null 2>&1; then xpan_bounded "$@" 2>/dev/null
    else timeout -s KILL 15 "$@" 2>/dev/null; fi
}
xpan_parent_observe() {
    XP_PARENT_HASH=; XP_PARENT_INODE=; XP_PARENT_KIND=
    [ "$#" = 4 ] || return 1
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    local context kind lines inode hash walk
    context=$(
        set -o pipefail || exit 1
        xpan_parent_bounded head -c 1048577 "$1" |
        LC_ALL=C xpan_parent_bounded awk -v BINMODE=3 -v info="$1" -v stock="$2" -v parent="$3" -v partition="$4" '
        function reject() { bad=1; exit 1 }
        function under(p,b) { return p==b || b=="/" || index(p,b "/")==1 }
        function path(p) {
            return length(p)<=4096 && p ~ /^\// && p !~ /[[:cntrl:][:space:]:,\\]/ &&
                p !~ /\/\// && p !~ /(^|\/)\.\.?($|\/)/ && (p=="/" || p !~ /\/$/)
        }
        function encoded(p,c) { c=p; gsub(/\\(040|134)/,"_",c); return c !~ /\\/ }
        function rawpath(p) {
            return p ~ /^\// && encoded(p) && p !~ /\/\// && p !~ /(^|\/)\.\.?($|\/)/ && (p=="/" || p !~ /\/$/)
        }
        function options(s,a,n,i,k,r,seen) {
            n=split(s,a,","); r=0
            for(i=1;i<=n;i++) {
                k=a[i]; sub(/=.*/,"",k)
                if(k !~ /^[A-Za-z0-9_.-]+$/ || seen[k]++ || a[i]==k "=") return 0
                if(a[i]=="ro" || a[i]=="rw") r++
            }
            return r==1 && encoded(s)
        }
        BEGIN {
            if(!path(info) || !path(stock) || !path(parent) || !path(partition) || stock==parent ||
                parent==partition || !under(stock,parent) || !under(parent,partition)) reject()
            production=(stock=="/odm/etc/camera/config" && parent=="/odm/etc" && partition=="/odm")
            testbase=info; sub(/\/mountinfo$/,"",testbase)
            fixture=(info ~ /\/_tmp\/xpan-runtime-test\.[A-Za-z0-9_]+\/[A-Za-z0-9_.-]+\/mountinfo$/ &&
                under(stock,testbase) && under(parent,testbase) && (partition=="/" || under(partition,testbase)))
            kind="normal"
        }
        {
            bytes+=length($0)+1
            if(bytes>1048576 || NR>8192 || length($0)>4096 || NF<10 || $0 ~ /[[:cntrl:]]/ || $0 ~ /^ | $|  /) reject()
            sep=7; while(sep<=NF && $sep!="-") sep++
            if(NF!=sep+3 || $1 !~ /^[1-9][0-9]*$/ || $2 !~ /^(0|[1-9][0-9]*)$/ || $1==$2 || ids[$1]++ ||
                !rawpath($4) || !rawpath($5) || $(sep+1) !~ /^[A-Za-z0-9_.+-]+$/ || !encoded($(sep+2)) ||
                !options($6) || !options($(sep+3))) reject()
            if($3 !~ /^[0-9]+:[0-9]+$/ && !($3=="dev" && $(sep+1)=="mock" && $(sep+2)=="mock")) reject()
            for(i=7;i<sep;i++) {
                k=$i; sub(/:.*/,"",k)
                if($i !~ /^((shared|master|propagate_from):[0-9]+|unbindable)$/ || optional[NR,k]++) reject()
            }
            up[$1]=$2; at[$5]=$1; point[$1]=$5
            if(under(stock,$5)) {
                if($5!="/" && $5!=partition && $5!=parent) reject()
                if(ancestors[$5]++) reject()
                keep[NR]=$0
                if($5==parent) {
                    if(!(production || fixture) || $4!="/" || $(sep+1)!="overlay" || $(sep+2)!="KSU\\040" ||
                        ($6!="rw,relatime" && $6!="ro,relatime")) reject()
                    for(i=7;i<sep;i++) if($i !~ /^(shared|master):[0-9]+$/) reject()
                    super=$(sep+3)
                    if(super !~ /^ro,seclabel,lowerdir=\/mnt\/vendor\/[A-Za-z0-9_-]+\/odm\/etc:\/odm\/etc$/) reject()
                    kind="ksu"
                }
            } else if(under($5,stock) && $5!=stock "/camera_unit_config" &&
                $5!=stock "/camera_unit_feature_config.protobuf" && $5!=stock "/oplus_camera_config") reject()
        }
        END {
            if(bad || !("/" in at) || !(partition in at)) exit 1
            root=at["/"]; part=at[partition]
            if(up[root] in ids) exit 1
            if(partition!="/" && up[part]!=root) exit 1
            if((parent in at) && up[at[parent]]!=part) exit 1
            print kind
            for(i=1;i<=NR;i++) if(i in keep) print keep[i]
        }'
    ) || return 1
    kind=${context%%'
'*}; lines=${context#*'
'}
    case "$kind" in normal|ksu) ;; *) return 1 ;; esac
    [ -n "$lines" ] || return 1
    walk=$2
    while :; do
        [ -d "$walk" ] && [ ! -L "$walk" ] || return 1
        [ "$walk" != / ] || break
        walk=${walk%/*}; [ -n "$walk" ] || walk=/
    done
    inode=$(xpan_parent_bounded stat -Lc '%d:%i' "$2") || return 1
    case "$inode" in ''|*[!0-9:]*|:*|*:|*:*:*) return 1 ;; esac
    case "$inode" in *:*) ;; *) return 1 ;; esac
    hash=$(printf '%s\n' "$lines" | xpan_parent_bounded sha256sum) || return 1
    hash=${hash%% *}
    [ "${#hash}" = 64 ] || return 1
    case "$hash" in *[!0-9a-f]*) return 1 ;; esac
    XP_PARENT_HASH=$hash; XP_PARENT_INODE=$inode; XP_PARENT_KIND=$kind
    return 0
} 2>/dev/null
