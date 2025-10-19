#!/bin/bash
set -euo pipefail

# HakoFoundry Unraid Device Discovery & Template Customizer
TEMPLATE_URL="https://raw.githubusercontent.com/HakoForge/HakoFoundry/main/unraid-templates/HakoFoundry.xml"
OUTPUT_FILE="HakoFoundry-custom.xml"
BASE_TEMPLATE="HakoFoundry-base.xml"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${BLUE}=== HakoFoundry Unraid Device Discovery ===${NC}\n"

generate_secret() {
    local random_bytes
    random_bytes=$(openssl rand -base64 32 2>/dev/null || head -c 32 /dev/urandom | base64)
    echo "$random_bytes" | tr '+/' '-_' | tr -d '='
}

discover_block_devices() {
    echo -e "${CYAN}🔍 Scanning for storage devices...${NC}"
    local devices=() count=0
    while IFS= read -r line; do
    local name size type
    name=$(awk '{print $1}' <<<"$line")
    size=$(awk '{print $2}' <<<"$line")
    type=$(awk '{print $3}' <<<"$line")
    if [[ "$type" != "part" && "$name" != loop* && "$name" != ram* ]]; then
        devices+=("/dev/$name|$size"); ((count++))
    fi
    done < <(lsblk -d -n -o NAME,SIZE,TYPE 2>/dev/null | grep -E '^sd|^nvme|^mmcblk')
    echo "Found $count storage devices:"
    for di in "${devices[@]}"; do
    echo "  - $(cut -d'|' -f1 <<<"$di") ($(cut -d'|' -f2 <<<"$di"))"
    done
    printf '%s\n' "${devices[@]}"
}

discover_serial_devices() {
    echo -e "${CYAN}🔍 Scanning for serial devices...${NC}"
    local devices=() count=0
    for device in /dev/ttyACM* /dev/ttyUSB* /dev/ttyS*; do
    [[ -c "$device" ]] || continue
    devices+=("$device"); ((count++))
    done
    echo "Found $count serial devices:"
    for d in "${devices[@]}"; do echo "  - $d"; done
    printf '%s\n' "${devices[@]}"
}

ask_yes_no() {
    local prompt="$1" default="${2:-n}" response
    while true; do
    if [[ "$default" == "y" ]]; then
        read -r -p "$prompt [Y/n]: " response; response=${response:-y}
    else
        read -r -p "$prompt [y/N]: " response; response=${response:-n}
    fi
    case "$response" in [Yy]*) return 0;; [Nn]*) return 1;; *) echo "Please answer yes or no.";; esac
    done
}

download_template() {
    echo -e "${CYAN}📥 Downloading base template...${NC}"
    if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$BASE_TEMPLATE" "$TEMPLATE_URL" || return 1
    elif command -v wget >/dev/null 2>&1; then
    wget -qO "$BASE_TEMPLATE" "$TEMPLATE_URL" || return 1
    else
    echo -e "${RED}Error: Neither curl nor wget found.${NC}"; return 1
    fi
    [[ -s "$BASE_TEMPLATE" ]] || return 1
    echo "✓ Template downloaded"
}

embed_fallback_template() {
    cat > "$BASE_TEMPLATE" <<'EOF'
<?xml version="1.0"?>
<Container version="2">
    <Name>HakoFoundry</Name>
    <Repository>hakoforge/hako-foundry:latest</Repository>
    <Registry>https://hub.docker.com/r/hakoforge/hako-foundry</Registry>
    <Network>bridge</Network>
    <MyIP/>
    <Shell>bash</Shell>
    <Privileged>false</Privileged>
    <Support>https://github.com/HakoForge/HakoFoundry</Support>
    <Project>https://github.com/HakoForge/HakoFoundry</Project>
    <Overview>HakoFoundry - Disk Imaging and Hardware Management Tool</Overview>
    <Category>Tools:System</Category>
    <WebUI>http://[IP]:[PORT:8080]/</WebUI>
    <Icon>https://raw.githubusercontent.com/HakoForge/HakoFoundry/main/assets/images/icon.png</Icon>
    <ExtraParams>--cap-add=SYS_RAWIO</ExtraParams>

    <Networking>
    <Mode>bridge</Mode>
    <Publish>
        <Port>
        <HostPort>8080</HostPort>
        <ContainerPort>8080</ContainerPort>
        <Protocol>tcp</Protocol>
        </Port>
    </Publish>
    </Networking>

    <Data>
    <Volume>
        <HostDir>/mnt/user/appdata/hako-foundry</HostDir>
        <ContainerDir>/app/config</ContainerDir>
        <Mode>rw</Mode>
    </Volume>
    <Volume>
        <HostDir>/sys/class/thermal</HostDir>
        <ContainerDir>/sys/class/thermal</ContainerDir>
        <Mode>ro</Mode>
    </Volume>
    <Volume>
        <HostDir>/sys/class/hwmon</HostDir>
        <ContainerDir>/sys/class/hwmon</ContainerDir>
        <Mode>ro</Mode>
    </Volume>
    </Data>

    <Config Name="Web UI Port" Target="8080" Default="8080" Mode="tcp" Description="Web interface port" Type="Port" Display="always" Required="true" Mask="false">8080</Config>
    <Config Name="Configuration Directory" Target="/app/config" Default="/mnt/user/appdata/hako-foundry" Mode="rw" Description="Configuration directory" Type="Path" Display="always" Required="true" Mask="false">/mnt/user/appdata/hako-foundry</Config>
    <Config Name="Thermal Monitoring" Target="/sys/class/thermal" Default="/sys/class/thermal" Mode="ro" Description="System thermal information" Type="Path" Display="advanced" Required="false" Mask="false">/sys/class/thermal</Config>
    <Config Name="Hardware Monitoring" Target="/sys/class/hwmon" Default="/sys/class/hwmon" Mode="ro" Description="Hardware monitoring information" Type="Path" Display="advanced" Required="false" Mask="false">/sys/class/hwmon</Config>
    <Config Name="Open Access" Target="OPEN_ACCESS" Default="false" Mode="" Description="Enable open access" Type="Variable" Display="always" Required="true" Mask="false">false</Config>
    <Config Name="Secret Key" Target="SECRET" Default="" Mode="" Description="Secret key" Type="Variable" Display="always" Required="false" Mask="true"></Config>
    <Config Name="User ID" Target="PUID" Default="99" Mode="" Description="User ID" Type="Variable" Display="always" Required="true" Mask="false">99</Config>
    <Config Name="Group ID" Target="PGID" Default="100" Mode="" Description="Group ID" Type="Variable" Display="always" Required="true" Mask="false">100</Config>
</Container>
EOF
}

# Unraid check
if [[ ! -f /etc/unraid-version ]]; then
    echo -e "${YELLOW}Warning: Intended for Unraid. Detected: $(uname -s)${NC}"
    ask_yes_no "Continue anyway?" "n" || exit 0
    echo
fi

# Download or fallback
download_template || { echo -e "${YELLOW}Using embedded template fallback...${NC}"; embed_fallback_template; }
echo

# Backup existing output
if [[ -f "$OUTPUT_FILE" ]]; then
    BACKUP_FILE="${OUTPUT_FILE}.backup.$(date +%Y%m%d_%H%M%S)"
    cp -f "$OUTPUT_FILE" "$BACKUP_FILE"
    echo -e "${YELLOW}✓ Backed up ${OUTPUT_FILE} → ${BACKUP_FILE}${NC}\n"
fi

# Discover devices
echo -e "${GREEN}🔎 Device Discovery${NC}\n"
STORAGE_DEVICES="$(discover_block_devices)"; echo
SERIAL_DEVICES="$(discover_serial_devices)"; echo

# Secret + config
echo -e "${GREEN}🔐 Generating Security Configuration${NC}"
SECRET="$(generate_secret)"
echo "Generated secret key: ${SECRET:0:6}********"
echo

echo -e "${GREEN}⚙️  Configuration Options${NC}"
# default if non-interactive
if [[ ! -t 0 ]]; then
    WEB_PORT="8080"; OPEN_ACCESS="false"; PUID="99"; PGID="100"
else
    read -r -p "Web UI port [8080]: " WEB_PORT; WEB_PORT="${WEB_PORT:-8080}"
    OPEN_ACCESS="false"; ask_yes_no "Enable open access (no authentication)?" "n" && OPEN_ACCESS="true"
    echo; echo "Unraid defaults: PUID=99 (nobody), PGID=100 (users)"
    read -r -p "User ID (PUID) [99]: " PUID; PUID="${PUID:-99}"
    read -r -p "Group ID (PGID) [100]: " PGID; PGID="${PGID:-100}"
fi
echo

echo -e "${GREEN}📝 Generating Custom Template${NC}"
cp -f "$BASE_TEMPLATE" "$OUTPUT_FILE"

# SECRET (only if empty)
sed -i '/Config Name="Secret Key"/ s/Default=""/Default='"\"$SECRET\""'/;' "$OUTPUT_FILE"
sed -i '/Config Name="Secret Key"/ s|> *</Config>|>'"$SECRET"'</Config>|' "$OUTPUT_FILE"

# Web UI port (value + Default)
sed -i '/Config Name="Web UI Port"/ s/>[0-9]\+<\/Config>/>'"$WEB_PORT"'<\/Config>/' "$OUTPUT_FILE"
sed -i '/Config Name="Web UI Port"/ s/Default="[^"]*"/Default="'"$WEB_PORT"'"/' "$OUTPUT_FILE"

# OPEN_ACCESS (value + Default)
sed -i '/Config Name="Open Access"/ s/>\(true\|false\)<\/Config>/>'"$OPEN_ACCESS"'<\/Config>/' "$OUTPUT_FILE"
sed -i '/Config Name="Open Access"/ s/Default="\(true\|false\)"/Default="'"$OPEN_ACCESS"'"/' "$OUTPUT_FILE"

# PUID/PGID (value + Default)
sed -i '/Config Name="User ID"/ s/>[0-9]\+<\/Config>/>'"$PUID"'<\/Config>/' "$OUTPUT_FILE"
sed -i '/Config Name="User ID"/ s/Default="[^"]*"/Default="'"$PUID"'"/' "$OUTPUT_FILE"
sed -i '/Config Name="Group ID"/ s/>[0-9]\+<\/Config>/>'"$PGID"'<\/Config>/' "$OUTPUT_FILE"
sed -i '/Config Name="Group ID"/ s/Default="[^"]*"/Default="'"$PGID"'"/' "$OUTPUT_FILE"

generate_device_configs() {
    local dtype="$1" dlist="$2" xml="" i=1
    if [[ -n "$dlist" ]]; then
    while IFS='|' read -r devpath devsize; do
        [[ -n "$devpath" ]] || continue
        local desc
        if [[ "$dtype" == "storage" && -n "$devsize" ]]; then
        desc="Storage device mapping - $devpath ($devsize)"
        elif [[ "$dtype" == "storage" ]]; then
        desc="Storage device mapping - $devpath"
        else
        desc="Serial device mapping - $devpath"
        fi
        xml+="\n  <Config Name=\"${dtype^} Device $i\" Target=\"$devpath\" Default=\"\" Mode=\"\" Description=\"$desc\" Type=\"Device\" Display=\"advanced\" Required=\"false\" Mask=\"false\">$devpath</Config>"
        ((i++))
    done <<< "$dlist"
    fi
    echo -e "$xml"
}

STORAGE_CONFIGS="$(generate_device_configs "storage" "$STORAGE_DEVICES")"
SERIAL_CONFIGS="$(generate_device_configs "serial" "$SERIAL_DEVICES")"

if [[ -n "$STORAGE_CONFIGS$SERIAL_CONFIGS" ]]; then
    tmpfile="$(mktemp)"
    {
    [[ -n "$STORAGE_CONFIGS" ]] && { echo -e "\n  <!-- Auto-discovered Storage Devices -->"; echo -e "$STORAGE_CONFIGS"; }
    [[ -n "$SERIAL_CONFIGS"  ]] && { echo -e "\n  <!-- Auto-discovered Serial Devices -->";  echo -e "$SERIAL_CONFIGS";  }
    echo
    } > "$tmpfile"
    # insert before closing tag
    sed -i '$d' "$OUTPUT_FILE"
    cat "$tmpfile" >> "$OUTPUT_FILE"
    echo "</Container>" >> "$OUTPUT_FILE"
    rm -f "$tmpfile"
fi

# Timestamp comment
TS="$(date "+%Y-%m-%d %H:%M:%S")"
sed -i "2i<!-- Generated by HakoFoundry Device Discovery Script on $TS -->" "$OUTPUT_FILE"

# Optional XML validation
if command -v xmllint >/dev/null 2>&1; then
    xmllint --noout "$OUTPUT_FILE" && echo "XML validated (xmllint)."
fi

echo -e "${GREEN}✅ Custom template generated: $OUTPUT_FILE${NC}\n"

# Summary
echo -e "${BLUE}📋 Configuration Summary${NC}"
echo "  Template: $OUTPUT_FILE"
echo "  Web Port: $WEB_PORT"
echo "  Open Access: $OPEN_ACCESS"
echo "  User ID: $PUID"
echo "  Group ID: $PGID"
echo "  Secret: ${SECRET:0:6}********"
[[ -n "$STORAGE_DEVICES" ]] && echo "  Storage Devices: $(wc -l <<<"$STORAGE_DEVICES" | xargs)"
[[ -n "$SERIAL_DEVICES"  ]] && echo "  Serial Devices:  $(wc -l <<<"$SERIAL_DEVICES"  | xargs)"

echo -e "\n${YELLOW}Next Steps:${NC}
1) Review $OUTPUT_FILE
2) Unraid Docker tab → Add Container
3) Template dropdown → Import Template
4) Upload $OUTPUT_FILE → Apply

${GREEN}Access after start: http://YOUR-UNRAID-IP:$WEB_PORT${NC}"

# Cleanup
rm -f "$BASE_TEMPLATE"