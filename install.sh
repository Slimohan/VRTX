#!/bin/sh
# VRTX installer. Run it from the VRTX download (normally ~/VRTX):
#   1. Creates your VRTX folder in the Klipper config folder
#      (~/printer_data/config/VRTX) with your one config file, vrtx.cfg,
#      and a link core/ -> this download, and adds  [include VRTX/vrtx.cfg]
#      to printer.cfg. Your file is never overwritten. An older install with
#      numbered files (00_core.cfg ...) is joined into vrtx.cfg.
#      A new vrtx.cfg is built from the template (config/vrtx.cfg) and three
#      questions: which calibration method you prefer (Nudge probe or camera;
#      the other method stays in the file, switched off), what kind of tool
#      changer it is (full toolheads, or a hotend swapper whose motor and part
#      cooling fans stay on the carriage) and how many tools you have (the
#      tools, docks and T commands are written for that many).
#   2. Links the optional Z switch module into Klipper and Kalico.
#   3. Sets up the VRTX page: a service, the page at http://<printer>/vrtx/
#      through the web server of Mainsail and/or Fluidd, a VRTX entry in
#      Mainsail's sidebar (Fluidd has none), and VRTX in the Moonraker update
#      panel. Needs sudo for the service and nginx.
#
# Usage: ./install.sh [path to klipper or kalico, ...]   (default: ~/klipper ~/kalico)
#   CONF=/path/to/config ./install.sh   if your config folder is not ~/printer_data/config
#   COPY=1 ./install.sh                 to copy the module instead of symlinking it
#   CALIBRATION=nudge|camera CHANGER=full|hotend TOOLS=4 ./install.sh   to answer the questions up front
#                                       (without a terminal, and without these, you get nudge and 10 tools)
#   NO_WEB=1 ./install.sh               to skip step 3
#   NGINX_SITE=/etc/nginx/sites-available/<name> ./install.sh   if your Mainsail or Fluidd site file has another name
#
# Several printers on one computer (KIAUH instances, ~/printer_<NAME>_data):
#   CONF=~/printer_<NAME>_data/config ./install.sh
# reads Moonraker's port from that instance's moonraker.conf and gives the
# instance its own page service (vrtx-<NAME>), page port and address
# (http://<computer>/vrtx-<NAME>/). The update panel and the Mainsail sidebar
# show plain VRTX. Run it once per instance.
# Running it again is safe: every step checks what is already there.
set -e
here=$(cd "$(dirname "$0")" && pwd)
conf="${CONF:-$HOME/printer_data/config}"
conf="${conf%/}"
. "$here/instance.sh"
if [ -n "$inst" ]; then
    echo "instance $inst: Moonraker port $mport, VRTX service $name, page port $pport"
fi

# --- 1. your VRTX folder -----------------------------------------------------
if [ ! -f "$conf/printer.cfg" ]; then
    echo "no printer.cfg in $conf - set CONF=/path/to/config and run again"
    exit 1
fi
case "$here/" in
    "$conf"/*) echo "This VRTX download is inside the config folder ($here)."
               echo "Clone it into your home folder instead:  cd ~ && git clone https://github.com/Slimohan/VRTX"
               exit 1 ;;
esac
dest="$conf/VRTX"
mkdir -p "$dest"
if [ -e "$dest/core" ] && [ ! -L "$dest/core" ]; then
    echo "$dest/core exists and is not a link - move it away and run again"
    exit 1
fi
ln -sfn "$here" "$dest/core"
echo "VRTX folder: $dest (core -> $here)"
# Your files from an older install:
# - dock positions that lived next to printer.cfg
if [ -f "$conf/vrtx_dock_positions.cfg" ] && [ ! -e "$dest/vrtx.cfg" ] && [ ! -e "$dest/20_dock_positions.cfg" ]; then
    mv "$conf/vrtx_dock_positions.cfg" "$dest/20_dock_positions.cfg"
    echo "moved your vrtx_dock_positions.cfg into $dest"
fi
# - the numbered files (00_core.cfg ... 90_settings.cfg): joined into one vrtx.cfg
#   in the order Klipper used to load them, the old files go to VRTX/old/
old=$(ls "$dest"/[0-9][0-9]_*.cfg 2>/dev/null || true)
if [ -n "$old" ] && [ ! -e "$dest/vrtx.cfg" ]; then
    mkdir -p "$dest/old"
    {
        echo "# VRTX: your configuration. This is the ONE file you edit."
        echo "# Joined from your numbered files by the installer, in the order they loaded."
        echo "# The originals are in the old/ folder next to this file. The current template,"
        echo "# with the explanations for every section, is built by:  python3 core/config/build_config.py"
        echo
    } > "$dest/vrtx.cfg"
    for f in $old; do
        fname=$(basename "$f")
        { echo "# ===== from $fname ====="; cat "$f"; echo; } >> "$dest/vrtx.cfg"
        mv "$f" "$dest/old/$fname"
    done
    echo "joined your numbered files into $dest/vrtx.cfg (originals: $dest/old/)"
fi
# vrtx.cfg is built from the template with your two answers (a new install only,
# an existing vrtx.cfg is never touched, and then the questions are not asked).
build="$here/config/build_config.py"
ask_number() {   # $1 = the question, $2 = lowest, $3 = highest answer; the answer is in $reply
    while :; do
        printf '%s ' "$1"
        read -r reply || { reply=""; return 1; }
        case "$reply" in
            ''|*[!0-9]*) ;;
            *) if [ "$reply" -ge "$2" ] && [ "$reply" -le "$3" ]; then return 0; fi ;;
        esac
        echo "  Please type a number from $2 to $3."
    done
}
# [save_variables]: in this printer's config folder, or the one printer.cfg already has
if grep -qs '^\[save_variables\]' "$conf/printer.cfg"; then
    varargs="--own-save-variables"
else
    varargs="--variables $conf/variables.cfg"
fi
if [ ! -e "$dest/vrtx.cfg" ]; then
    command -v python3 >/dev/null 2>&1 || { echo "python3 is needed to build vrtx.cfg"; exit 1; }
    calibration="$CALIBRATION"
    changer="$CHANGER"
    tools="$TOOLS"
    case "$calibration" in
        1|[Nn]udge) calibration=nudge ;;
        2|[Cc]amera|[Vv]isual) calibration=camera ;;
        '') ;;
        *) echo "CALIBRATION must be nudge or camera"; exit 1 ;;
    esac
    case "$changer" in
        1|[Ff]ull) changer=full ;;
        2|[Hh]otend) changer=hotend ;;
        '') ;;
        *) echo "CHANGER must be full or hotend"; exit 1 ;;
    esac
    case "$tools" in
        '') ;;
        *[!0-9]*) echo "TOOLS must be a number from 1 to 10"; exit 1 ;;
        *) if [ "$tools" -lt 1 ] || [ "$tools" -gt 10 ]; then echo "TOOLS must be a number from 1 to 10"; exit 1; fi ;;
    esac
    if [ -z "$calibration" ]; then
        if [ -t 0 ]; then
            echo
            ask_number "Which calibration method do you prefer, Nudge or Visual? 1 for Nudge 2 for Camera" 1 2 || reply=1
            [ "$reply" = 2 ] && calibration=camera || calibration=nudge
        else
            calibration=nudge
            echo "no terminal: calibration method nudge (CALIBRATION=camera chooses the camera method)"
        fi
    fi
    if [ -z "$changer" ]; then
        if [ -t 0 ]; then
            echo "What kind of tool changer is it?"
            echo "  1  Full toolheads: every tool has its own extruder motor and part cooling fan"
            echo "  2  Hotend swapper: a tool is only a hotend, thermistor and hotend fan;"
            echo "     the extruder motor and part cooling fans stay on the carriage"
            ask_number "1 for full toolheads, 2 for a hotend swapper" 1 2 || reply=1
            [ "$reply" = 2 ] && changer=hotend || changer=full
        else
            changer=full
            echo "no terminal: full toolheads (CHANGER=hotend for a hotend swapper)"
        fi
    fi
    if [ -z "$tools" ]; then
        if [ -t 0 ]; then
            ask_number "How many tools are you using? 1-10" 1 10 || reply=10
            tools=$reply
        else
            tools=10
            echo "no terminal: 10 tools (TOOLS=4 for four tools)"
        fi
    fi
    python3 "$build" --calibration "$calibration" --changer "$changer" --tools "$tools" $varargs -o "$dest/vrtx.cfg"
    echo "  created vrtx.cfg for $tools tool(s), $changer tool changer, calibration method $calibration - this is the one file you edit"
elif grep -q "^# Joined from your numbered files" "$dest/vrtx.cfg" && [ -d "$dest/old" ]; then
    :   # a joined file always differs from the template, nothing to compare
elif command -v python3 >/dev/null 2>&1; then
    # the current template, built the way this vrtx.cfg was built, for you to compare
    rec=$(python3 "$build" --record-of "$dest/vrtx.cfg")
    if [ -n "$rec" ]; then
        calibration=${rec%% *}; rest=${rec#* }
        tools=${rest%% *}; changer=${rest#* }
    else
        calibration=nudge     # a vrtx.cfg from before the installer built it: the old, full template
        tools=10 changer=full
    fi
    newtmp=$(mktemp)
    python3 "$build" --calibration "$calibration" --changer "$changer" --tools "$tools" $varargs -o "$newtmp"
    if cmp -s "$newtmp" "$dest/vrtx.cfg"; then
        rm -f "$newtmp"
    else
        mv "$newtmp" "$dest/vrtx.cfg.new"
        echo "  kept your vrtx.cfg (the current template, built for $tools tool(s), $changer tool changer and the $calibration method, is saved as vrtx.cfg.new)"
    fi
fi
# the one line in printer.cfg
if grep -q "^\[include VRTX/vrtx\.cfg\]" "$conf/printer.cfg"; then
    echo "printer.cfg already includes VRTX/vrtx.cfg"
elif grep -q "^\[include VRTX/\*\.cfg\]" "$conf/printer.cfg"; then
    cp "$conf/printer.cfg" "$conf/printer.cfg.vrtx-backup"
    sed -i "s|^\[include VRTX/\*\.cfg\]|[include VRTX/vrtx.cfg]|" "$conf/printer.cfg"
    echo "printer.cfg: [include VRTX/*.cfg] changed to [include VRTX/vrtx.cfg] (backup: printer.cfg.vrtx-backup)"
else
    cp "$conf/printer.cfg" "$conf/printer.cfg.vrtx-backup"
    { echo "[include VRTX/vrtx.cfg]"; cat "$conf/printer.cfg.vrtx-backup"; } > "$conf/printer.cfg"
    echo "added [include VRTX/vrtx.cfg] at the top of printer.cfg (backup: printer.cfg.vrtx-backup)"
fi
if grep -qE "^\[include (vrtx/|vrtx_dock_positions)" "$conf/printer.cfg"; then
    echo "NOTE: printer.cfg still has includes from an older VRTX install:"
    grep -nE "^\[include (vrtx/|vrtx_dock_positions)" "$conf/printer.cfg" | sed 's/^/    /'
    echo "      remove them, VRTX/vrtx.cfg loads everything now"
fi
stray=$(ls "$dest"/*.cfg 2>/dev/null | grep -v "/vrtx\.cfg$" || true)
if [ -n "$stray" ]; then
    echo "NOTE: only vrtx.cfg is loaded from $dest. These files are NOT loaded any more:"
    echo "$stray" | sed 's/^/    /'
    echo "      add an [include ...] line at the end of vrtx.cfg for any you still need"
fi

# --- 2. Z switch module ------------------------------------------------------
[ $# -eq 0 ] && set -- "$HOME/klipper" "$HOME/kalico"
found=0
for tree in "$@"; do
    xdest="$tree/klippy/extras"
    [ -d "$xdest" ] || continue
    found=1
    for f in "$here"/klippy_extras/*.py; do
        fname=$(basename "$f")
        rm -f "$xdest/$fname"
        if [ -n "$COPY" ]; then cp "$f" "$xdest/$fname"; else ln -s "$f" "$xdest/$fname"; fi
    done
    echo "installed the Z switch module into $xdest"
done
[ $found -eq 1 ] || echo "no klippy/extras folder found, Z switch module not installed (only needed with [vrtx_z_probe]; pass the path to Klipper or Kalico)"

# --- 3. the VRTX page ------------------------------------------------------
if [ -z "$NO_WEB" ]; then
    echo "--- VRTX page"
    user=$(id -un)
    unit=/etc/systemd/system/$name.service
    # An earlier version called the service vrtx-ui; Moonraker only accepts the name "vrtx".
    if [ -z "$inst" ] && [ -f /etc/systemd/system/vrtx-ui.service ]; then
        sudo systemctl disable --now vrtx-ui >/dev/null 2>&1 || true
        sudo rm -f /etc/systemd/system/vrtx-ui.service
        echo "renamed the old service vrtx-ui to vrtx"
    fi
    # Converting a hand-made setup: the single "vrtx" service edited to talk to
    # this instance's Moonraker becomes $name, and its update panel entry,
    # Moonraker permission and /vrtx/ route go (the route only when no default
    # instance in ~/printer_data uses it).
    drop_default=0
    if [ -n "$inst" ] && [ -f /etc/systemd/system/vrtx.service ] && grep -q "127.0.0.1:$mport" /etc/systemd/system/vrtx.service; then
        echo "found the service vrtx pointed at this instance's Moonraker (port $mport): replacing it with $name"
        sudo systemctl disable --now vrtx >/dev/null 2>&1 || true
        sudo rm -f /etc/systemd/system/vrtx.service
        sudo systemctl daemon-reload
        if [ -f "$conf/moonraker.conf" ] && grep -q "^\[update_manager vrtx\]" "$conf/moonraker.conf"; then
            cp "$conf/moonraker.conf" "$conf/moonraker.conf.vrtx-backup"
            python3 - "$conf/moonraker.conf" "[update_manager vrtx]" <<'PY'
import sys
path, header = sys.argv[1], sys.argv[2]
out, skip = [], False
for line in open(path).read().splitlines(True):
    if line.strip() == header:
        skip = True
        while out and not out[-1].strip():
            out.pop()
        continue
    if skip and line.lstrip().startswith("["):
        skip = False
        out.append("\n")
    if not skip:
        out.append(line)
open(path, "w").write("".join(out).rstrip("\n") + "\n")
PY
            echo "update panel: removed the old [update_manager vrtx] of this instance (backup: moonraker.conf.vrtx-backup)"
        fi
        [ -f "$datadir/moonraker.asvc" ] && sed -i "/^vrtx$/d" "$datadir/moonraker.asvc"
        [ -f "$HOME/printer_data/config/printer.cfg" ] || drop_default=1
    fi

    tmp=$(mktemp)
    cat > "$tmp" <<UNIT
[Unit]
Description=VRTX tool page ($name)
After=network.target $msvc.service

[Service]
User=$user
ExecStart=/usr/bin/python3 $here/ui/vrtx_server.py --port $pport --bind 127.0.0.1 --moonraker http://127.0.0.1:$mport
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
    if sudo install -m 644 "$tmp" "$unit" && sudo systemctl daemon-reload && sudo systemctl enable --now "$name" >/dev/null 2>&1; then
        sudo systemctl restart "$name"
        echo "service $name installed and running (user $user, page port $pport, Moonraker port $mport)"
    else
        echo "could not install the service (sudo?), see README 'The web page' for the manual steps"
    fi
    rm -f "$tmp"

    # Let Moonraker restart the service (needed for the update panel).
    asvc="$(dirname "$conf")/moonraker.asvc"
    if [ -f "$asvc" ]; then
        [ -z "$inst" ] && sed -i "/^vrtx-ui$/d" "$asvc"
        if ! grep -qx "$name" "$asvc"; then
            echo "$name" >> "$asvc"
            echo "added $name to $asvc"
        fi
    fi

    # The page at /vrtx/ (or /vrtx-<NAME>/) inside the nginx site of Mainsail and/or Fluidd.
    # The default instance uses the snippet in this download; an instance gets its
    # own snippet in /etc/nginx/vrtx/ (kept out of the download, so updates stay clean).
    if [ -z "$inst" ]; then
        snippet="$here/ui/nginx-vrtx.conf"
    else
        snippet="/etc/nginx/vrtx/$name.conf"
        tmp=$(mktemp)
        sed -e "s|/vrtx|/$name|g" -e "s|127.0.0.1:8099|127.0.0.1:$pport|" "$here/ui/nginx-vrtx.conf" > "$tmp"
        sudo mkdir -p /etc/nginx/vrtx && sudo install -m 644 "$tmp" "$snippet" || echo "nginx: could not write $snippet (sudo?)"
        rm -f "$tmp"
    fi
    if [ -n "$NGINX_SITE" ]; then
        sites="$NGINX_SITE"
    else
        sites=""
        for site_name in mainsail fluidd; do
            [ -f "/etc/nginx/sites-available/$site_name" ] && sites="$sites /etc/nginx/sites-available/$site_name"
        done
    fi
    if [ -z "$sites" ]; then
        echo "nginx: no Mainsail or Fluidd site found in /etc/nginx/sites-available, the page is only on this machine at port $pport (set NGINX_SITE=/etc/nginx/sites-available/<name> and run again)"
    fi
    for site in $sites; do
        if [ $drop_default -eq 1 ] && [ -f "$site" ] && grep -q "ui/nginx-vrtx.conf" "$site"; then
            sudo cp "$site" "$site.vrtx-backup"
            sudo sed -i "\|ui/nginx-vrtx.conf|d" "$site"
            echo "nginx: removed the old /vrtx/ route from $site (backup: $site.vrtx-backup)"
        fi
        if [ ! -f "$site" ]; then
            echo "nginx: $site not found (set NGINX_SITE=... to the right file and run again)"
        elif grep -qF "$snippet" "$site"; then
            echo "nginx: /$name/ already set up in $site"
        else
            sudo cp "$site" "$site.$name-backup"
            # insert the include as the first line inside the server block
            if sudo sed -i "0,/server[[:space:]]*{/s||&\n    include $snippet;  # VRTX page ($name)|" "$site" && sudo nginx -t >/dev/null 2>&1; then
                sudo systemctl reload nginx
                echo "nginx: the page is at http://<printer>/$name/ via $site (backup: $site.$name-backup)"
            else
                sudo cp "$site.$name-backup" "$site"
                echo "nginx: could not add /$name/, $site restored unchanged"
            fi
        fi
    done

    # The VRTX entry in Mainsail's sidebar (.theme/navi.json, merged with yours).
    # Fluidd has no such feature; skipped when Mainsail is not installed.
    mainsail=0
    [ -d "$HOME/mainsail" ] && mainsail=1
    case " $sites " in *mainsail*) mainsail=1 ;; esac
    [ -n "$NGINX_SITE" ] && mainsail=1
    if [ $mainsail -eq 0 ]; then
        echo "sidebar: Mainsail not found, no sidebar entry (open http://<printer>/vrtx/ or bookmark it)"
    elif [ -f "$conf/printer.cfg" ]; then
        mkdir -p "$conf/.theme"
        python3 - "$conf/.theme/navi.json" "$here/examples/mainsail/navi.json" "/$name/" <<'PY'
import json, os, sys
path, template, href = sys.argv[1], sys.argv[2], sys.argv[3]
entry = json.load(open(template))[0]
entry["href"] = href
entries = []
if os.path.exists(path):
    try:
        entries = json.load(open(path))
    except ValueError:
        print("sidebar: %s is not valid JSON, left unchanged" % path)
        sys.exit(0)
entries = [e for e in entries if e.get("title") != entry["title"]] + [entry]
json.dump(entries, open(path, "w"), indent=2)
print("sidebar: VRTX entry in %s (reload Mainsail to see it)" % path)
PY
    fi

    # VRTX in the Moonraker update panel of this instance. Each printer has its
    # own moonraker.conf, so the entry is always the plain [update_manager vrtx].
    # Moonraker only lets an entry restart a service with the entry's own name,
    # so a page service with a printer name (vrtx-<NAME>) is not listed: it
    # restarts itself when an update changes its code.
    mconf="$conf/moonraker.conf"
    managed="klipper"
    [ "$name" = vrtx ] && managed="klipper vrtx"
    if [ -f "$mconf" ]; then
        if [ "$name" != vrtx ] && grep -q "^\[update_manager $name\]" "$mconf"; then
            cp "$mconf" "$mconf.vrtx-backup"
            awk -v h="[update_manager $name]" '$0==h{skip=1;next} /^\[/{skip=0} !skip' "$mconf.vrtx-backup" > "$mconf"
            echo "update panel: [update_manager $name] renamed to [update_manager vrtx] (backup: moonraker.conf.vrtx-backup)"
        fi
        if grep -q "^\[update_manager vrtx\]" "$mconf"; then
            if [ -z "$inst" ] && grep -q "^managed_services:.*vrtx-ui" "$mconf"; then
                cp "$mconf" "$mconf.vrtx-backup"
                sed -i "s/^\(managed_services:.*\)vrtx-ui/\1vrtx/" "$mconf"
                sudo systemctl restart "$msvc" 2>/dev/null || true
                echo "update panel: fixed managed_services in moonraker.conf (backup: moonraker.conf.vrtx-backup), Moonraker restarted"
            elif [ "$name" != vrtx ] && grep -q "^managed_services:.*vrtx-" "$mconf"; then
                cp "$mconf" "$mconf.vrtx-backup"
                sed -i "s/^managed_services:.*vrtx-.*$/managed_services: $managed/" "$mconf"
                sudo systemctl restart "$msvc" 2>/dev/null || true
                echo "update panel: fixed managed_services in moonraker.conf (backup: moonraker.conf.vrtx-backup), Moonraker restarted"
            else
                echo "update panel: already in moonraker.conf"
            fi
        else
            cat >> "$mconf" <<CONF

[update_manager vrtx]
type: git_repo
path: $here
origin: https://github.com/Slimohan/VRTX.git
primary_branch: main
managed_services: $managed
CONF
            sudo systemctl restart "$msvc" 2>/dev/null || true
            echo "update panel: VRTX added to moonraker.conf as [update_manager vrtx], $msvc restarted"
        fi
    fi
fi

echo
echo "Next: edit $dest/vrtx.cfg (Mainsail: Machine > VRTX > vrtx.cfg; Fluidd: Configuration > VRTX), section by section."
echo "Move your [tool Tn], tool heater/fan and [toolchanger] sections from printer.cfg into it."
echo "Then restart Klipper and run VRTX_CHECK."
[ -z "$NO_WEB" ] && echo "VRTX page: http://<printer>/$name/ (Mainsail: reload and click VRTX in the sidebar; Fluidd: bookmark the address)."
