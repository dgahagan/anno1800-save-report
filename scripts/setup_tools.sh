#!/usr/bin/env bash
# Build the third-party decoders into vendor/ (gitignored). Run once per machine.
#   anno-mods/RdaConsole   unpacks the .a7s RDA container
#   anno-mods/FileDBReader decodes the inner FileDB blobs to XML
#   NiHoel/Anno1800SavegameVisualizer (MIT) supplies the a7s_all.xml rule file
# Neither anno-mods repo declares a licence: local use only, never commit or redistribute the output.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/vendor/src"; BIN="$ROOT/vendor/bin"
mkdir -p "$SRC" "$BIN"
TFM="${DOTNET_TFM:-net$(dotnet --version | cut -d. -f1).0}"   # the installed SDK's major, e.g. net10.0

# Each tool is pinned to the commit this project was tested with. To move a pin: change the SHA here,
# run this script, run the tests, then update the table in README.md.
clone() {  # url dir sha
  [ -d "$SRC/$2/.git" ] || git clone -q "$1" "$SRC/$2"
  if [ "$(git -C "$SRC/$2" rev-parse HEAD)" != "$3" ]; then
    git -C "$SRC/$2" cat-file -e "$3^{commit}" 2>/dev/null || git -C "$SRC/$2" fetch -q origin
    git -C "$SRC/$2" checkout -q -f "$3"      # -f drops the retarget edits below; they are applied again
  fi
  git -C "$SRC/$2" submodule update -q --init --recursive
}
clone https://github.com/anno-mods/RdaConsole.git                  RdaConsole                 97ec11e98394bf1a4ee2c478b91271e6e066731b  # 2024-11-28
clone https://github.com/anno-mods/FileDBReader.git                FileDBReader               fae10393e29e095e338dfdd3596663616311c5d8  # 2026-10-15
clone https://github.com/NiHoel/Anno1800SavegameVisualizer.git     Anno1800SavegameVisualizer bb40bfcb38e8426e2aed0079561cdcdf894fd356  # 2024-04-06

# Both tools target net6.0, which has no runtime on current Fedora; retarget to the installed SDK.
sed -i "s#<TargetFramework>net[0-9.]*</TargetFramework>#<TargetFramework>$TFM</TargetFramework>#" \
  "$SRC/RdaConsole/RdaConsole/RdaConsole.csproj" \
  "$SRC/FileDBReader/FileDBReader.csproj" \
  "$SRC/FileDBReader/FileDBSerializer/FileDBSerializer/AnnoMods.BBDom.csproj"
# BBSerializer implements the obsolete IFormatter; newer SDKs make that an error.
grep -q SYSLIB0011 "$SRC/FileDBReader/FileDBSerializer/FileDBSerializer/AnnoMods.BBDom.csproj" || \
  sed -i 's#<Nullable>enable</Nullable>#<Nullable>enable</Nullable>\n    <NoWarn>$(NoWarn);SYSLIB0011;SYSLIB0050;SYSLIB0051</NoWarn>#' \
  "$SRC/FileDBReader/FileDBSerializer/FileDBSerializer/AnnoMods.BBDom.csproj"

dotnet publish "$SRC/RdaConsole/RdaConsole/RdaConsole.csproj" -c Release -o "$BIN/RdaConsole" -nologo -v q
dotnet publish "$SRC/FileDBReader/FileDBReader.csproj" -c Release -o "$BIN/FileDBReader" -nologo -v q
"$BIN/RdaConsole/RdaConsole" --version && "$BIN/FileDBReader/FileDBReader" --version
echo "tools built in $BIN"
