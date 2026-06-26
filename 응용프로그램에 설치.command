#!/bin/sh
# LinkNote.app 을 /Applications 로 복사(설치). 더블클릭하세요.
cd "$(dirname "$0")" || exit 1
APP="./desktop/src-tauri/target/release/bundle/macos/LinkNote.app"

if [ ! -d "$APP" ]; then
  echo "빌드된 LinkNote.app 을 찾지 못했습니다. 먼저 'npm run tauri build' 를 실행하세요."
  exit 1
fi

echo "응용프로그램 폴더로 복사 중..."
rm -rf "/Applications/LinkNote.app"
cp -R "$APP" "/Applications/"
echo "설치 완료 → /Applications/LinkNote.app"
echo "Dock 고정: 응용프로그램 폴더에서 LinkNote 를 Dock 으로 한 번 끌어다 놓으면 됩니다."
open /Applications
