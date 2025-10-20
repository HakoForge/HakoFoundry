#!/bin/bash

# Interactive script to generate docker-compose.yml with dynamic device discovery and architecture detection

OUTPUT_FILE="docker-compose.yml"
BACKUP_FILE="docker-compose.yml.backup"
BUILD_LOCAL="false"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Portable in-place sed helper (GNU vs BSD)
sed_inplace() {
    if sed --version >/dev/null 2>&1; then
        sed -i "$@"
    else
        sed -i '' "$@"
    fi
}

# Function to detect system architecture
detect_architecture() {
    local arch=""

    # Method 1: Use uname -m (most reliable)
    arch=$(uname -m 2>/dev/null)

    case "$arch" in
        x86_64|amd64)
            echo "amd64"
            return 0
            ;;
        aarch64|arm64)
            echo "arm64"
            return 0
            ;;
        armv7l|armhf)
            echo "arm32"
            return 0
            ;;
        *)
            # Method 2: Try dpkg if available (Debian/Ubuntu)
            if command -v dpkg >/dev/null 2>&1; then
                arch=$(dpkg --print-architecture 2>/dev/null)
                case "$arch" in
                    amd64) echo "amd64"; return 0 ;;
                    arm64) echo "arm64"; return 0 ;;
                    armhf) echo "arm32"; return 0 ;;
                esac
            fi

            # Method 3: Try /proc/cpuinfo
            if [ -f /proc/cpuinfo ]; then
                if grep -q "Intel\|AMD" /proc/cpuinfo 2>/dev/null; then
                    echo "amd64"
                    return 0
                elif grep -q "ARM\|aarch64" /proc/cpuinfo 2>/dev/null; then
                    echo "arm64"
                    return 0
                fi
            fi

            # Method 4: Check Docker platform if Docker is available
            if command -v docker >/dev/null 2>&1; then
                local docker_arch=$(docker version --format '{{.Server.Arch}}' 2>/dev/null)
                case "$docker_arch" in
                    amd64) echo "amd64"; return 0 ;;
                    arm64) echo "arm64"; return 0 ;;
                    arm) echo "arm32"; return 0 ;;
                esac
            fi

            # Fallback
            echo "unknown"
            return 1
            ;;
    esac
}

# Function to get Docker image recommendation
get_docker_image() {
    local arch="$1"
    local preference="$2"

    case "$preference" in
        "multiarch")
            echo "hakoforge/hako-foundry:latest"
            ;;
        "specific")
            case "$arch" in
                amd64)
                    echo "hakoforge/hako-foundry:latest"
                    ;;
                arm64)
                    echo "hakoforge/hako-foundry:arm64"
                    ;;
                arm32)
                    echo "hakoforge/hako-foundry:latest"
                    ;;
                *)
                    echo "hakoforge/hako-foundry:latest"
                    ;;
            esac
            ;;
        *)
            echo "hakoforge/hako-foundry:latest"
            ;;
    esac
}

# Function to generate a compatible secret
generate_secret() {
    # Generate URL-safe base64 secret (no problematic characters)
    local random_bytes=$(openssl rand -base64 32 2>/dev/null || head -c 32 /dev/urandom | base64)
    # Make it URL-safe by replacing problematic characters
    echo "$random_bytes" | tr '+/' '-_' | tr -d '='
}

# Function to ask yes/no questions
ask_yes_no() {
    local prompt="$1"
    local default="$2"
    local response

    while true; do
        if [ "$default" = "y" ]; then
            read -p "$prompt [Y/n]: " response
            response=${response:-y}
        else
            read -p "$prompt [y/N]: " response
            response=${response:-n}
        fi

        case $response in
            [Yy]* ) return 0;;
            [Nn]* ) return 1;;
            * ) echo "Please answer yes or no.";;
        esac
    done
}

# Function to discover block devices
discover_block_devices() {
    echo "      # Dynamically discovered block devices ($(date))"
    local count=0
    lsblk -d -n -o NAME,SIZE | grep '^sd' | while read device size; do
        echo "      - \"/dev/$device:/dev/$device\"  # $size"
        count=$((count + 1))
    done
    if [ $count -eq 0 ]; then
        echo "      # No block devices found ($(date))"
    fi
}

# Function to discover serial devices
discover_serial_devices() {
    local found=false

    # Check for ACM devices
    for device in /dev/ttyACM*; do
        if [ -e "$device" ]; then
            if [ "$found" = false ]; then
                echo "      # Dynamically discovered serial devices ($(date))"
                found=true
            fi
            echo "      - \"$device:$device\""
        fi
    done

    # Check for USB devices
    for device in /dev/ttyUSB*; do
        if [ -e "$device" ]; then
            if [ "$found" = false ]; then
                echo "      # Dynamically discovered serial devices ($(date))"
                found=true
            fi
            echo "      - \"$device:$device\""
        fi
    done

    if [ "$found" = false ]; then
        echo "      # No serial devices found ($(date))"
    fi
}

echo -e "${BLUE}=== Hako Foundry Docker Compose Generator ===${NC}"
echo ""

# Detect system architecture
DETECTED_ARCH=$(detect_architecture)
echo -e "${CYAN}🔍 System Architecture Detection${NC}"
echo "Detected architecture: ${GREEN}$DETECTED_ARCH${NC}"

# Explain image options based on architecture
case "$DETECTED_ARCH" in
    "amd64")
        echo "✅ Full compatibility"
        DEFAULT_IMAGE_PREFERENCE="multiarch"
        ;;
    "arm64")
        echo "⚡ ARM64 native images available for better performance"
        echo "   - Multi-arch: hakoforge/hako-foundry:latest"
        echo "   - ARM64 image: hakoforge/hako-foundry:arm64"
        DEFAULT_IMAGE_PREFERENCE="specific"
        ;;
    "arm32")
        echo "⚠️  ARM32 support via emulation (may be slower)"
        DEFAULT_IMAGE_PREFERENCE="multiarch"
        ;;
    "unknown")
        echo "⚠️  Unknown architecture - using default image"
        DEFAULT_IMAGE_PREFERENCE="multiarch"
        ;;
esac
echo ""

# Backup existing file if it exists
if [ -f "$OUTPUT_FILE" ]; then
    cp "$OUTPUT_FILE" "$BACKUP_FILE"
    echo -e "${YELLOW}Backed up existing $OUTPUT_FILE to $BACKUP_FILE${NC}"
    echo ""
fi

# 1. Docker Image Selection
echo -e "${GREEN}1. Docker Image Selection${NC}"
if [ "$DETECTED_ARCH" = "arm64" ]; then
    echo "Choose image strategy for ARM64:"
    echo "  1. Multi-arch image (may use emulation)"
    echo "  2. Native ARM64 image"
    echo "  3. Build your own (use local Dockerfile)"
    echo ""

    read -p "Select option (1/2/3) [2]: " IMAGE_CHOICE
    IMAGE_CHOICE=${IMAGE_CHOICE:-2}

    case $IMAGE_CHOICE in
        1)
            DOCKER_IMAGE="hakoforge/hako-foundry:latest"
            echo "Selected: Multi-arch image"
            ;;
        2)
            DOCKER_IMAGE="hakoforge/hako-foundry:arm64"
            echo "Selected: Native ARM64 image"
            ;;
        3)
            BUILD_LOCAL="true"
            echo "Selected: Build from local Dockerfile"
            ;;
        *)
            DOCKER_IMAGE="hakoforge/hako-foundry:arm64"
            echo "Defaulting to: Native ARM64 image"
            ;;
    esac
else
    echo "Choose image strategy for $DETECTED_ARCH:"
    echo "  1. Recommended multi-arch image (hakoforge/hako-foundry:latest)"
    echo "  2. Enter custom image"
    echo "  3. Build your own (use local Dockerfile)"
    echo ""

    read -p "Select option (1/2/3) [1]: " IMAGE_CHOICE
    IMAGE_CHOICE=${IMAGE_CHOICE:-1}

    case $IMAGE_CHOICE in
        1)
            DOCKER_IMAGE="hakoforge/hako-foundry:latest"
            echo "Selected: multi-arch image"
            ;;
        2)
            read -p "Enter custom image: " DOCKER_IMAGE
            echo "Selected: Custom image ($DOCKER_IMAGE)"
            ;;
        3)
            BUILD_LOCAL="true"
            echo "Selected: Build from local Dockerfile"
            ;;
        *)
            DOCKER_IMAGE="hakoforge/hako-foundry:latest"
            echo "Defaulting to: multi-arch image"
            ;;
    esac
fi
echo ""

# 2. Environment Configuration
echo -e "${GREEN}2. Environment Configuration${NC}"
if [ -f ".env" ]; then
    DEFAULT_ENV_CHOICE=1
else
    DEFAULT_ENV_CHOICE=2
fi
echo "How would you like to supply environment variables?"
echo "  1. Use a .env file (recommended for portability)"
echo "  2. Define variables directly in docker-compose.yml"
read -p "Select option (1/2) [${DEFAULT_ENV_CHOICE}]: " ENV_CHOICE
ENV_CHOICE=${ENV_CHOICE:-$DEFAULT_ENV_CHOICE}
case $ENV_CHOICE in
    1)
        ENV_SOURCE="file"
        echo "Selected: .env file (compose will reference .env)"
        ;;
    2)
        ENV_SOURCE="compose"
        echo "Selected: define variables in docker-compose.yml"
        ;;
    *)
        ENV_SOURCE="file"
        echo "Defaulting to: .env file"
        ;;
esac
echo ""

if [ "$ENV_SOURCE" = "compose" ]; then
    echo -e "${GREEN}3. Generating secure secret...${NC}"
    SECRET=$(generate_secret)
    echo "Generated secret: $SECRET"
    echo ""
fi

# 4. Ask about storage type
echo -e "${GREEN}4. Storage Configuration${NC}"
echo "Choose storage type for configuration data:"
echo "  - Docker Volume: Managed by Docker, automatic cleanup, portable"
echo "  - Bind Mount: Direct host path, easier backup/access, persistent"
echo ""

STORAGE_TYPE=""
STORAGE_VALUE=""

if ask_yes_no "Use Docker volume instead of bind mount?" "n"; then
    STORAGE_TYPE="volume"
    read -p "Enter volume name [hako_config]: " STORAGE_VALUE
    STORAGE_VALUE=${STORAGE_VALUE:-hako_config}
    echo "Will use Docker volume: $STORAGE_VALUE"
else
    STORAGE_TYPE="bind"
    CURRENT_DIR=$(pwd)
    read -p "Enter bind mount path [$CURRENT_DIR]: " STORAGE_VALUE
    STORAGE_VALUE=${STORAGE_VALUE:-$CURRENT_DIR}
    echo "Will use bind mount: $STORAGE_VALUE"
fi
echo ""

# 5. Ask for port
echo -e "${GREEN}5. Network Configuration${NC}"
read -p "Enter host port [8080]: " HOST_PORT
HOST_PORT=${HOST_PORT:-8080}
echo "Will expose on port: $HOST_PORT"
echo ""

# 6. Ask about user/group configuration
echo -e "${GREEN}6. User/Group Configuration${NC}"
echo "Configure user and group IDs for file permissions:"
echo "  - Set PUID/PGID: Run container with specific user/group (recommended for file access)"
echo "  - Skip: Use container defaults (simpler but may cause permission issues)"
echo ""

USE_USER_CONFIG="false"
PUID=""
PGID=""

if ask_yes_no "Configure user and group IDs (PUID/PGID)?" "y"; then
    USE_USER_CONFIG="true"
    echo ""
    echo "Current user ID: $(id -u)"
    echo "Current group ID: $(id -g)"
    echo ""

    read -p "Enter PUID [$(id -u)]: " PUID
    PUID=${PUID:-$(id -u)}

    read -p "Enter PGID [$(id -g)]: " PGID
    PGID=${PGID:-$(id -g)}

    echo "Will use PUID=$PUID, PGID=$PGID"
else
    echo "Will use container default user/group"
fi
echo ""

# 7. Ask about open access
echo -e "${GREEN}7. Security Configuration${NC}"
echo "Open Access allows unrestricted access to the web interface."
echo "  - true: No authentication required (convenient but less secure)"
echo "  - false: Authentication required (more secure)"
echo ""

OPEN_ACCESS="false"
if ask_yes_no "Enable open access (no authentication)?" "n"; then
    OPEN_ACCESS="true"
fi
echo "Open access: $OPEN_ACCESS"
echo ""

# 8. Ask about auto-scanning drives
echo -e "${GREEN}8. Device Configuration${NC}"
echo "Auto-scan will automatically detect and add all available storage devices."
echo "This includes all /dev/sd* devices and serial ports (/dev/ttyACM*, /dev/ttyUSB*)."
echo ""

AUTOSCAN="false"
if ask_yes_no "Auto-scan for storage devices and serial ports?" "y"; then
    AUTOSCAN="true"
    echo ""
    echo -e "${YELLOW}Scanning for devices...${NC}"

    # Show what we found
    BLOCK_COUNT=$(lsblk -d -n -o NAME | grep '^sd' | wc -l)
    SERIAL_COUNT=0
    for device in /dev/ttyACM* /dev/ttyUSB*; do
        if [ -e "$device" ]; then
            SERIAL_COUNT=$((SERIAL_COUNT + 1))
        fi
    done

    echo "Found $BLOCK_COUNT block devices and $SERIAL_COUNT serial devices"

    if [ $BLOCK_COUNT -gt 0 ]; then
        echo "Block devices:"
        lsblk -d -n -o NAME,SIZE | grep '^sd' | head -5 | while read device size; do
            echo "  - /dev/$device ($size)"
        done
        if [ $BLOCK_COUNT -gt 5 ]; then
            echo "  ... and $((BLOCK_COUNT - 5)) more"
        fi
    fi

    if [ $SERIAL_COUNT -gt 0 ]; then
        echo "Serial devices:"
        for device in /dev/ttyACM* /dev/ttyUSB*; do
            if [ -e "$device" ]; then
                echo "  - $device"
            fi
        done
    fi
fi
echo ""

echo -e "${GREEN}9. Generating docker-compose.yml...${NC}"

# Generate the compose file header
cat > "$OUTPUT_FILE" << EOF
version: '3.8'

services:
  hako-foundry:
EOF

# Use build or image depending on selection
if [ "$BUILD_LOCAL" = "true" ]; then
  cat >> "$OUTPUT_FILE" << EOF
    build:
      context: .
      dockerfile: Dockerfile
    container_name: hako-foundry
    restart: unless-stopped
EOF
else
  cat >> "$OUTPUT_FILE" << EOF
    image: $DOCKER_IMAGE
    container_name: hako-foundry
    pull_policy: always
    restart: unless-stopped
EOF
fi

# Append ports and environment sections
cat >> "$OUTPUT_FILE" << EOF

    # Port mapping
    ports:
      - "$HOST_PORT:8080"
EOF

# Environment configuration (either .env file or inline environment)
if [ "$ENV_SOURCE" = "file" ]; then
    cat >> "$OUTPUT_FILE" << EOF

    # Environment variables via .env file
    env_file:
      - .env
EOF
else
    cat >> "$OUTPUT_FILE" << EOF

    # Environment variables defined in compose
    environment:
      - OPEN_ACCESS=$OPEN_ACCESS
      - SECRET=$SECRET
EOF
fi

# Add PUID/PGID only if configured and using compose-defined environment
if [ "$USE_USER_CONFIG" = "true" ] && [ "$ENV_SOURCE" = "compose" ]; then
    cat >> "$OUTPUT_FILE" << EOF
      - PUID=$PUID
      - PGID=$PGID
EOF
fi

cat >> "$OUTPUT_FILE" << EOF

    # Volumes
    volumes:
EOF

# Add storage configuration
if [ "$STORAGE_TYPE" = "volume" ]; then
    echo "      - $STORAGE_VALUE:/app/config/" >> "$OUTPUT_FILE"
else
    echo "      - $STORAGE_VALUE:/app/config/" >> "$OUTPUT_FILE"
fi

cat >> "$OUTPUT_FILE" << EOF
      - /sys/class/thermal:/sys/class/thermal:ro
      - /sys/class/hwmon:/sys/class/hwmon:ro

    # Capabilities
    cap_add:
      - SYS_RAWIO
EOF

# Add devices section if autoscan is enabled
if [ "$AUTOSCAN" = "true" ]; then
    echo "" >> "$OUTPUT_FILE"
    echo "    # Device mapping" >> "$OUTPUT_FILE"
    echo "    devices:" >> "$OUTPUT_FILE"
    discover_serial_devices >> "$OUTPUT_FILE"
    discover_block_devices >> "$OUTPUT_FILE"
else
    echo "" >> "$OUTPUT_FILE"
    echo "    # Device mapping (disabled - run with autoscan to populate)" >> "$OUTPUT_FILE"
    echo "    # devices:" >> "$OUTPUT_FILE"
    echo "    #   - \"/dev/sda:/dev/sda\"  # Add devices manually as needed" >> "$OUTPUT_FILE"
fi

# Add volumes section if using Docker volume
if [ "$STORAGE_TYPE" = "volume" ]; then
    cat >> "$OUTPUT_FILE" << EOF

# Named volumes
volumes:
  $STORAGE_VALUE:
    driver: local
EOF
else
    cat >> "$OUTPUT_FILE" << EOF

# No named volumes needed (using bind mount)
EOF
fi

# If env source is .env, ensure file exists and contains required keys
if [ "$ENV_SOURCE" = "file" ]; then
    ENV_FILE_STATUS="unchanged"

    if [ ! -f .env ]; then
        GENERATED_SECRET=$(generate_secret)
        {
            echo "# Auto-generated by setup.sh on $(date)"
            echo "SECRET=$GENERATED_SECRET"
            echo "OPEN_ACCESS=$OPEN_ACCESS"
            echo "DEBUG=false"
            if [ "$USE_USER_CONFIG" = "true" ]; then
                echo "PUID=$PUID"
                echo "PGID=$PGID"
            fi
        } > .env
        ENV_FILE_STATUS="created"
    else
        # Update or add keys as needed
        # SECRET: generate if missing or placeholder/empty
        if grep -q '^SECRET=' .env; then
            CURRENT_SECRET=$(grep '^SECRET=' .env | tail -1 | cut -d= -f2-)
            if [ -z "$CURRENT_SECRET" ] || [[ "$CURRENT_SECRET" == "your-very-secret-string" ]]; then
                NEW_SECRET=$(generate_secret)
                sed_inplace "s/^SECRET=.*/SECRET=$NEW_SECRET/" .env
                ENV_FILE_STATUS="updated"
            fi
        else
            echo "SECRET=$(generate_secret)" >> .env
            ENV_FILE_STATUS="updated"
        fi

        # OPEN_ACCESS: sync to chosen value
        if grep -q '^OPEN_ACCESS=' .env; then
            sed_inplace "s/^OPEN_ACCESS=.*/OPEN_ACCESS=$OPEN_ACCESS/" .env
            ENV_FILE_STATUS="updated"
        else
            echo "OPEN_ACCESS=$OPEN_ACCESS" >> .env
            ENV_FILE_STATUS="updated"
        fi

        # DEBUG: ensure present (default false)
        if ! grep -q '^DEBUG=' .env; then
            echo "DEBUG=false" >> .env
            ENV_FILE_STATUS="updated"
        fi

        # PUID/PGID: only if configured
        if [ "$USE_USER_CONFIG" = "true" ]; then
            if grep -q '^PUID=' .env; then
                sed_inplace "s/^PUID=.*/PUID=$PUID/" .env
            else
                echo "PUID=$PUID" >> .env
            fi
            if grep -q '^PGID=' .env; then
                sed_inplace "s/^PGID=.*/PGID=$PGID/" .env
            else
                echo "PGID=$PGID" >> .env
            fi
            ENV_FILE_STATUS="updated"
        fi
    fi
fi

echo -e "${GREEN}✓ Generated $OUTPUT_FILE successfully!${NC}"
echo ""
echo -e "${BLUE}Configuration Summary:${NC}"
echo "  Architecture: $DETECTED_ARCH"
if [ "$BUILD_LOCAL" = "true" ]; then
  echo "  Build: context=., dockerfile=Dockerfile"
else
  echo "  Docker Image: $DOCKER_IMAGE"
fi
if [ "$ENV_SOURCE" = "compose" ]; then
    echo "  Secret: $SECRET"
else
    case "$ENV_FILE_STATUS" in
        created)
            echo "  Env source: .env (created with generated secret)"
            ;;
        updated)
            echo "  Env source: .env (updated to match selections)"
            ;;
        *)
            echo "  Env source: .env (no changes)"
            ;;
    esac
fi
echo "  Storage: $STORAGE_TYPE ($STORAGE_VALUE)"
echo "  Port: $HOST_PORT"
if [ "$USE_USER_CONFIG" = "true" ]; then
    echo "  User/Group: PUID=$PUID, PGID=$PGID"
else
    echo "  User/Group: Container defaults"
fi
echo "  Open Access: $OPEN_ACCESS"
echo "  Device Auto-scan: $AUTOSCAN"
if [ "$AUTOSCAN" = "true" ]; then
    echo "  Devices Found: $BLOCK_COUNT block + $SERIAL_COUNT serial"
fi
echo ""

# Architecture-specific notes
if [ "$DETECTED_ARCH" = "arm64" ]; then
    if [ "$BUILD_LOCAL" = "true" ]; then
        echo -e "${CYAN}📝 ARM64 Notes:${NC}"
        echo "  • Building locally from Dockerfile (ARM64 host)"
    elif [[ "$DOCKER_IMAGE" == *"hakoforge"* ]]; then
        echo -e "${CYAN}📝 ARM64 Notes:${NC}"
        echo "  • Using proven ARM64-native image for optimal performance"
        echo "  • No emulation overhead - runs natively on your ARM64 system"
    else
        echo -e "${CYAN}📝 ARM64 Notes:${NC}"
        echo "  • Using multi-arch image (may use emulation)"
        echo "  • For better performance, consider the native ARM64 image:"
        echo "    image: hakoforge/hako-foundry:arm64"
    fi
fi
echo ""

# Get server IP for final instructions
get_server_ip() {
    local ip=""

    # Method 1: Try ip route (Linux)
    if command -v ip >/dev/null 2>&1; then
        ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' 2>/dev/null | head -1)
        if [[ -n "$ip" && "$ip" != "127.0.0.1" ]]; then
            echo "$ip"
            return
        fi
    fi

    # Method 2: Try hostname -I (Linux)
    if command -v hostname >/dev/null 2>&1; then
        ip=$(hostname -I 2>/dev/null | awk '{print $1}' | grep -v '^127\.' | head -1)
        if [[ -n "$ip" && "$ip" != "127.0.0.1" ]]; then
            echo "$ip"
            return
        fi
    fi

    # Method 3: Try ifconfig (macOS/Linux/Unix)
    if command -v ifconfig >/dev/null 2>&1; then
        # Look for active interfaces with inet addresses (not loopback)
        ip=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | awk '{print $2}' | head -1)
        # Clean up potential "addr:" prefix on some systems
        ip=$(echo "$ip" | sed 's/addr://')
        if [[ -n "$ip" && "$ip" != "127.0.0.1" ]]; then
            echo "$ip"
            return
        fi
    fi

    # Method 4: Try netstat (Windows/cross-platform)
    if command -v netstat >/dev/null 2>&1; then
        # Get default route and extract local IP
        ip=$(netstat -rn 2>/dev/null | awk '/^0\.0\.0\.0/ {print $NF; exit}' | head -1)
        if [[ -n "$ip" && "$ip" != "127.0.0.1" && "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "$ip"
            return
        fi
    fi

    # Method 5: Try Windows ipconfig (if running under WSL or Git Bash)
    if command -v ipconfig.exe >/dev/null 2>&1; then
        ip=$(ipconfig.exe 2>/dev/null | grep "IPv4 Address" | head -1 | awk -F: '{print $2}' | tr -d ' \r\n')
        if [[ -n "$ip" && "$ip" != "127.0.0.1" ]]; then
            echo "$ip"
            return
        fi
    fi

    # Method 6: Try Windows route command (if available)
    if command -v route.exe >/dev/null 2>&1; then
        ip=$(route.exe print 2>/dev/null | grep "0.0.0.0.*0.0.0.0" | head -1 | awk '{print $4}')
        if [[ -n "$ip" && "$ip" != "127.0.0.1" ]]; then
            echo "$ip"
            return
        fi
    fi

    # Method 7: Try using netstat differently for Windows
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        ip=$(netstat -rn 2>/dev/null | grep "^0\.0\.0\.0" | awk '{print $4}' | head -1)
        if [[ -n "$ip" && "$ip" != "127.0.0.1" ]]; then
            echo "$ip"
            return
        fi
    fi

    # Method 8: Try to detect common private network ranges
    for interface_ip in $(hostname -I 2>/dev/null || echo ""); do
        # Check for common private IP ranges
        if [[ "$interface_ip" =~ ^192\.168\. ]] || [[ "$interface_ip" =~ ^10\. ]] || [[ "$interface_ip" =~ ^172\.(1[6-9]|2[0-9]|3[0-1])\. ]]; then
            echo "$interface_ip"
            return
        fi
    done

    # Method 9: Last resort - check for any non-loopback IP
    if command -v hostname >/dev/null 2>&1; then
        ip=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^127\.' | grep -v '^::1' | head -1)
        if [[ -n "$ip" ]]; then
            echo "$ip"
            return
        fi
    fi

    # Final fallback
    echo "localhost"
}

SERVER_IP=$(get_server_ip)

echo -e "${YELLOW}Next steps:${NC}"
echo "1. Review the generated docker-compose.yml file"
if [ "$STORAGE_TYPE" = "bind" ]; then
    echo "2. Ensure the directory $STORAGE_VALUE exists and has proper permissions"
    echo -e "3. Run: ${GREEN}docker compose up -d${NC}"
    echo -e "4. Access at: ${BLUE}http://$SERVER_IP:$HOST_PORT${NC}"
else
    echo -e "2. Run: ${GREEN}docker compose up -d${NC}"
    echo -e "3. Access at: ${BLUE}http://$SERVER_IP:$HOST_PORT${NC}"
fi
echo ""
echo -e "${YELLOW}To regenerate with different settings, run this script again.${NC}"
